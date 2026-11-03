"""Job handlers and the worker loop. Each handler is idempotent: re-running after a crash is safe."""

import json
import logging
import threading
import time
from collections.abc import Callable

from goulash import db
from goulash.collector import ingest, queue
from goulash.collector.adapters import ADAPTERS
from goulash.collector.extract import extract_site
from goulash.collector.fetch import Blocked, Fetcher, FetchError, FetchRequest, build_fetcher
from goulash.collector.html import Page, parse_html
from goulash.collector.snapshots import SnapshotStore

log = logging.getLogger("goulash.worker")


class Context:
    def __init__(self, fetcher: Fetcher, snapshots: SnapshotStore):
        self.fetcher = fetcher
        self.snapshots = snapshots

    def get_page(self, url: str) -> tuple[Page, str]:
        resp = self.fetcher.fetch(FetchRequest(url))
        if resp.status >= 400:
            raise FetchError(f"HTTP {resp.status} for {url}", retryable=resp.status >= 500 or resp.status == 429)
        digest = self.snapshots.put(resp.body)
        return parse_html(resp.text, resp.final_url), digest


def register_sources(conn) -> None:
    for a in ADAPTERS.values():
        conn.execute(
            """INSERT INTO sources (id, title, entry_url, edition, enabled, terms_note) VALUES (%s,%s,%s,%s,%s,%s)
               ON CONFLICT (id) DO UPDATE SET title = excluded.title, entry_url = excluded.entry_url,
                   edition = excluded.edition, terms_note = excluded.terms_note""",
            (a.id, a.title, a.entry_url, a.edition, a.enabled_by_default, a.terms_note),
        )
    conn.commit()


def handle_crawl_listing(conn, ctx: Context, payload: dict) -> str:
    adapter = ADAPTERS[payload["source"]]
    src = conn.execute("SELECT checkpoint FROM sources WHERE id = %s", (adapter.id,)).fetchone()
    checkpoint = (src or {}).get("checkpoint") or {}
    cards = 0
    for batch in adapter.listing(lambda u: ctx.get_page(u)[0], checkpoint, int(payload.get("max_pages", 3))):
        for url in batch.card_urls:
            ext = adapter.external_id(url)
            queue.enqueue(
                conn, "fetch_card", f"card:{adapter.id}:{adapter.edition}:{ext}", {"source": adapter.id, "url": url}
            )
            cards += 1
        conn.execute(
            "UPDATE sources SET checkpoint = %s, last_success = now(), last_error = NULL WHERE id = %s",
            (json.dumps(batch.checkpoint), adapter.id),
        )
        conn.commit()  # checkpoint after every page: a crash resumes from here
    return f"{cards} cards queued"


def handle_fetch_card(conn, ctx: Context, payload: dict) -> str:
    adapter = ADAPTERS[payload["source"]]
    page, _ = ctx.get_page(payload["url"])
    rec = adapter.parse_card(page)
    if rec is None:
        raise FetchError(f"card layout not recognised: {payload['url']}", retryable=False)
    sid, state = ingest.upsert_record(conn, rec)
    domain = ingest.domain_of(rec.website)
    if domain and rec.website:
        queue.enqueue(
            conn, "enrich_site", f"site:{domain}", {"supplier_id": sid, "url": rec.website}, refresh_after="30 days"
        )
    conn.commit()
    return f"{state} {rec.name}"


def handle_enrich_site(conn, ctx: Context, payload: dict) -> str:
    page, digest = ctx.get_page(payload["url"])
    first = extract_site(page)
    pages = [(first, digest)]
    for sub in first.subpages[:3]:
        try:
            p, d = ctx.get_page(sub)
            pages.append((extract_site(p), d))
        except FetchError as e:  # a broken subpage must not lose the homepage facts
            log.info("subpage %s: %s", sub, e)
    n = ingest.apply_site(conn, payload["supplier_id"], pages)
    conn.commit()
    return f"{n} facts from {len(pages)} pages"


HANDLERS: dict[str, Callable] = {
    "crawl_listing": handle_crawl_listing,
    "fetch_card": handle_fetch_card,
    "enrich_site": handle_enrich_site,
}


def run(kinds: list[str] | None = None, max_jobs: int = 100, threads: int = 1, fetcher: Fetcher | None = None) -> dict:
    kinds = kinds or list(HANDLERS)
    ctx = Context(fetcher or build_fetcher(max_requests=max_jobs * 5), SnapshotStore())
    stats = {"done": 0, "failed": 0, "busy": 0}
    lock = threading.Lock()

    def loop():
        conn = db.connect()
        try:
            while True:
                with lock:
                    if stats["done"] + stats["failed"] >= max_jobs:
                        return
                job = queue.claim(conn, kinds)
                if not job:
                    with lock:
                        others_busy = stats["busy"] > 0
                    if not others_busy:
                        return
                    time.sleep(1)  # a running job (e.g. a listing walk) may still enqueue work
                    continue
                with lock:
                    stats["busy"] += 1
                try:
                    msg = HANDLERS[job["kind"]](conn, ctx, job["payload"])
                    queue.complete(conn, job["id"])
                    log.info("ok %s #%s: %s", job["kind"], job["id"], msg)
                    key = "done"
                except Blocked as e:
                    conn.rollback()
                    queue.fail(conn, job, str(e), retryable=False)
                    log.warning("blocked %s #%s: %s", job["kind"], job["id"], e)
                    key = "failed"
                except FetchError as e:
                    conn.rollback()
                    queue.fail(conn, job, str(e), retryable=e.retryable)
                    log.warning("fail %s #%s: %s", job["kind"], job["id"], e)
                    key = "failed"
                except Exception as e:  # parser bug: keep the job for review, keep the worker alive
                    conn.rollback()
                    queue.fail(conn, job, f"{type(e).__name__}: {e}", retryable=False)
                    log.exception("error %s #%s", job["kind"], job["id"])
                    key = "failed"
                with lock:
                    stats[key] += 1
                    stats["busy"] -= 1
        finally:
            conn.close()

    workers = [threading.Thread(target=loop) for _ in range(threads)]
    for t in workers:
        t.start()
    for t in workers:
        t.join()
    stats.pop("busy")
    return stats
