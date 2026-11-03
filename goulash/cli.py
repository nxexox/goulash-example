"""Command line: `python -m goulash <command>`. The same commands run locally, in cron or in CI."""

import argparse
import gzip
import hashlib
import json
import logging
import sys
from pathlib import Path

from goulash import config, db, geo
from goulash.collector import ingest, queue, worker
from goulash.collector.adapters import ADAPTERS
from goulash.collector.html import parse_html
from goulash.taxonomy import categories_for_text

SEED_FILE = config.SEED_DIR / "catalog.jsonl.gz"


def cmd_init(args, conn):
    db.init_schema(conn)
    worker.register_sources(conn)
    print("schema ok")


def cmd_crawl(args, conn):
    """Queue a listing walk for each source, then process the queue."""
    for source in args.source or [a.id for a in ADAPTERS.values() if a.enabled_by_default]:
        queue.enqueue(
            conn,
            "crawl_listing",
            f"listing:{source}",
            {"source": source, "max_pages": args.pages},
            refresh_after="0 seconds",  # explicit run: always re-arm,
        )
    conn.commit()
    print(worker.run(max_jobs=args.max_jobs, threads=args.threads))
    print("vocabulary terms:", ingest.rebuild_vocabulary(conn))


def cmd_work(args, conn):
    print(worker.run(kinds=args.kind, max_jobs=args.max_jobs, threads=args.threads))
    print("vocabulary terms:", ingest.rebuild_vocabulary(conn))


def cmd_import_poc(args, conn):
    """Offline: ingest catalog cards saved by the PoC (PoC/poc/*.raw), no network."""
    root = Path(args.path)
    urls = [
        r["url"]
        for name in ("catalog_samples.json", "enrichment.json")
        for r in json.loads((root / name).read_text())
        if r.get("status") == 200
    ]
    counts: dict[str, int] = {}
    for url in urls:
        adapter = (
            ADAPTERS["prodexpo"] if "/exhibitors/" in url else ADAPTERS["worldfood"] if "/ExbDetails/" in url else None
        )
        if not adapter:
            continue
        raw = root / (hashlib.sha256(url.encode()).hexdigest()[:16] + ".raw")
        rec = adapter.parse_card(parse_html(raw.read_bytes().decode("utf-8", "replace"), url))
        if rec:
            _, state = ingest.upsert_record(conn, rec, observed_at="2026-11-04T00:00:00+05:00")
            counts[state] = counts.get(state, 0) + 1
    conn.commit()
    ingest.rebuild_vocabulary(conn)
    print(counts)


def cmd_export_seed(args, conn):
    """Dump the catalog (suppliers + contacts + facts + observations) to a portable seed file."""
    SEED_FILE.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with gzip.open(SEED_FILE, "wt", encoding="utf-8") as f:
        for s in conn.execute("SELECT * FROM suppliers ORDER BY key").fetchall():
            sid = s.pop("id")
            s.pop("search_vector")
            s["contacts"] = conn.execute(
                "SELECT kind, value, source_url, observed_at FROM contacts WHERE supplier_id=%s ORDER BY kind, value",
                (sid,),
            ).fetchall()
            s["facts"] = conn.execute(
                """SELECT field, value, evidence, source_url, snapshot_hash, observed_at, parser_version, method,
                          verification FROM facts WHERE supplier_id=%s ORDER BY field, value""",
                (sid,),
            ).fetchall()
            s["observations"] = conn.execute(
                """SELECT source, edition, external_id, source_url, payload_hash, payload, observed_at
                   FROM observations WHERE supplier_id=%s ORDER BY source, external_id""",
                (sid,),
            ).fetchall()
            f.write(json.dumps(s, ensure_ascii=False, default=str) + "\n")
            n += 1
    print(f"{n} suppliers -> {SEED_FILE}")


def load_seed(conn, path: Path = SEED_FILE) -> int:
    """Load seed into an empty database (first deploy on a fresh Neon/Postgres). Idempotent by supplier key."""
    if not path.exists():
        return 0
    n = 0
    with gzip.open(path, "rt", encoding="utf-8") as f, conn.transaction():
        for line in f:
            s = json.loads(line)
            contacts, facts, observations = s.pop("contacts"), s.pop("facts"), s.pop("observations")
            cols = list(s)
            row = conn.execute(
                f"""INSERT INTO suppliers ({", ".join(cols)}) VALUES ({", ".join("%s" for _ in cols)})
                    ON CONFLICT (key) DO NOTHING RETURNING id""",
                [s[c] for c in cols],
            ).fetchone()
            if not row:
                continue
            sid = row["id"]
            for c in contacts:
                conn.execute(
                    """INSERT INTO contacts (supplier_id, kind, value, source_url, observed_at)
                                VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                    (sid, c["kind"], c["value"], c["source_url"], c["observed_at"]),
                )
            for x in facts:
                conn.execute(
                    """INSERT INTO facts (supplier_id, field, value, evidence, source_url, snapshot_hash,
                                    observed_at, parser_version, method, verification)
                                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                    (
                        sid,
                        x["field"],
                        x["value"],
                        x["evidence"],
                        x["source_url"],
                        x["snapshot_hash"],
                        x["observed_at"],
                        x["parser_version"],
                        x["method"],
                        x["verification"],
                    ),
                )
            for o in observations:
                conn.execute(
                    """INSERT INTO observations (source, edition, external_id, source_url, payload_hash,
                                    payload, supplier_id, observed_at)
                                VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                    (
                        o["source"],
                        o["edition"],
                        o["external_id"],
                        o["source_url"],
                        o["payload_hash"],
                        json.dumps(o["payload"], ensure_ascii=False),
                        sid,
                        o["observed_at"],
                    ),
                )
            ingest.refresh_search_vector(conn, sid)
            n += 1
    ingest.rebuild_vocabulary(conn)
    return n


def cmd_load_seed(args, conn):
    print(f"{load_seed(conn, Path(args.path) if args.path else SEED_FILE)} suppliers loaded")


def cmd_reindex(args, conn):
    """Re-derive region/categories with the current dictionaries and rebuild the index. No network."""
    rows = conn.execute("SELECT id, name, city, address, description, rubrics, products FROM suppliers").fetchall()
    for s in rows:
        region = geo.region_of_city(s["city"]) or geo.region_from_address(s["address"])
        cat_text = " \n".join([s["name"], *s["rubrics"], *s["products"]])
        categories = categories_for_text(cat_text) or categories_for_text(s["description"] or "")
        conn.execute("UPDATE suppliers SET region = %s, categories = %s WHERE id = %s", (region, categories, s["id"]))
        ingest.refresh_search_vector(conn, s["id"])
    conn.commit()
    print(f"{len(rows)} suppliers, {ingest.rebuild_vocabulary(conn)} terms")


def cmd_stats(args, conn):
    q = {
        "suppliers": "SELECT count(*) AS n FROM suppliers",
        "with_site": "SELECT count(*) AS n FROM suppliers WHERE domain IS NOT NULL",
        "enriched": "SELECT count(*) AS n FROM suppliers WHERE enriched_at IS NOT NULL",
        "with_region": "SELECT count(*) AS n FROM suppliers WHERE region IS NOT NULL",
        "with_phone": "SELECT count(DISTINCT supplier_id) AS n FROM contacts WHERE kind='phone'",
    }
    out = {k: conn.execute(v).fetchone()["n"] for k, v in q.items()}
    out["facts"] = {
        r["field"]: r["n"]
        for r in conn.execute(
            "SELECT field, count(DISTINCT supplier_id) AS n FROM facts GROUP BY field ORDER BY field"
        ).fetchall()
    }
    out["jobs"] = {
        f"{r['kind']}:{r['status']}": r["n"]
        for r in conn.execute(
            "SELECT kind, status, count(*) AS n FROM jobs GROUP BY kind, status ORDER BY kind, status"
        ).fetchall()
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    p = argparse.ArgumentParser(prog="goulash")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="apply schema, register sources").set_defaults(fn=cmd_init)
    c = sub.add_parser("crawl", help="walk catalog listings and process resulting jobs")
    c.add_argument("--source", action="append", choices=list(ADAPTERS))
    c.add_argument("--pages", type=int, default=3, help="listing pages per source in this run")
    c.add_argument("--max-jobs", type=int, default=200)
    c.add_argument("--threads", type=int, default=4)
    c.set_defaults(fn=cmd_crawl)
    w = sub.add_parser("work", help="process queued jobs")
    w.add_argument("--kind", action="append", choices=list(worker.HANDLERS))
    w.add_argument("--max-jobs", type=int, default=200)
    w.add_argument("--threads", type=int, default=4)
    w.set_defaults(fn=cmd_work)
    i = sub.add_parser("import-poc", help="offline import of PoC snapshots")
    i.add_argument("--path", default=str(config.ROOT / "PoC" / "poc"))
    i.set_defaults(fn=cmd_import_poc)
    sub.add_parser("export-seed").set_defaults(fn=cmd_export_seed)
    s = sub.add_parser("load-seed")
    s.add_argument("--path")
    s.set_defaults(fn=cmd_load_seed)
    sub.add_parser("reindex").set_defaults(fn=cmd_reindex)
    sub.add_parser("stats").set_defaults(fn=cmd_stats)
    args = p.parse_args(argv)
    with db.connect() as conn:
        db.init_schema(conn)
        worker.register_sources(conn)
        args.fn(args, conn)
        conn.commit()


if __name__ == "__main__":
    sys.exit(main())
