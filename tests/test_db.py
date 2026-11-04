"""PostgreSQL integration: idempotent ingest, job queue leases, search ranking, seed round-trip, web pages."""

from goulash.collector import ingest, queue
from goulash.collector.adapters import CatalogRecord
from goulash.collector.extract import SiteExtraction, text_facts
from goulash.repo import load_cards
from goulash.search.engine import Filters, search


def rec(ext_id, name, website, city, rubrics, description="", source="prodexpo"):
    return CatalogRecord(
        source=source,
        edition="2026",
        external_id=ext_id,
        source_url=f"https://src/{ext_id}",
        name=name,
        website=website,
        city=city,
        rubrics=rubrics,
        description=description,
        phones=["+7 (495) 111-22-33"],
    )


def seed(conn):
    ids = {}
    for r in [
        rec("1", "СЫРОВАРНЯ УРАЛ", "https://syr-ural.ru", "Екатеринбург", ["Сыры"], "Моцарелла и сулугуни для пиццы"),
        rec("2", "МОЛОКО ПЛЮС", "https://moloko.ru", "Москва", ["Молочная продукция, мороженое, сыры"], "Творог"),
        rec("3", "ШОКОФАБРИКА", "https://choco.ru", "Москва", ["Шоколад и шоколадные изделия"]),
        rec("4", "УПАКПРОМ", "https://upak.ru", "Казань", ["Упаковка"], "Контейнеры с крышкой 500 мл"),
    ]:
        ids[r.name], _ = ingest.upsert_record(conn, r)
    ingest.apply_site(
        conn,
        ids["МОЛОКО ПЛЮС"],
        [
            (
                SiteExtraction(
                    url="https://moloko.ru",
                    facts=text_facts(
                        ["Доставка по Екатеринбургу и области", "Минимальная сумма заказа 10 000 руб."],
                        "https://moloko.ru",
                    ),
                ),
                "hash1",
            )
        ],
    )
    conn.commit()
    ingest.rebuild_vocabulary(conn)
    return ids


def test_ingest_is_idempotent_and_merges_by_domain(conn):
    r = rec("1", "СЫРОВАРНЯ УРАЛ", "https://www.syr-ural.ru/", "Екатеринбург", ["Сыры"])
    sid, state = ingest.upsert_record(conn, r)
    assert state == "inserted"
    assert ingest.upsert_record(conn, r) == (sid, "unchanged")
    other_source = rec("XYZ", "Сыроварня Урал", "http://syr-ural.ru", None, ["Сыры, творог"], source="worldfood")
    sid2, state2 = ingest.upsert_record(conn, other_source)
    assert (sid2, state2) == (sid, "updated")
    card = load_cards(conn, [sid])[0]
    assert card.city == "Екатеринбург" and card.region == "Свердловская область"
    assert len(card.sources) == 2
    assert [p["value"] for p in card.phones] == ["+7 (495) 111-22-33"]


def test_record_without_site_is_kept(conn):
    sid, _ = ingest.upsert_record(conn, rec("9", "БЕЗ САЙТА", None, None, ["Соки, нектары"]))
    assert load_cards(conn, [sid])[0].website is None


def test_queue_claim_lease_retry(conn):
    queue.enqueue(conn, "enrich_site", "site:a.ru", {"url": "https://a.ru"})
    queue.enqueue(conn, "enrich_site", "site:a.ru", {"url": "https://a.ru"})  # dedup
    conn.commit()
    job = queue.claim(conn, ["enrich_site"])
    assert job["attempts"] == 1
    assert queue.claim(conn, ["enrich_site"]) is None  # leased
    queue.fail(conn, job, "timeout", retryable=True)
    row = conn.execute("SELECT status, run_after > now() AS later FROM jobs").fetchone()
    assert row == {"status": "retry_pending", "later": True}
    conn.execute("UPDATE jobs SET lease_until = now() - interval '1 second', status = 'running', run_after = now()")
    conn.commit()
    assert queue.claim(conn, ["enrich_site"])["id"] == job["id"]  # expired lease is reclaimed


def test_search_exact_before_category_and_typo(conn):
    ids = seed(conn)
    r = search(conn, "моцарела")
    assert r.hits[0].card.id == ids["СЫРОВАРНЯ УРАЛ"] and r.hits[0].exact
    category_only = [h for h in r.hits if not h.exact]
    assert ids["МОЛОКО ПЛЮС"] in [h.card.id for h in category_only]

    r = search(conn, "шокалад")
    assert r.corrections and r.hits[0].card.id == ids["ШОКОФАБРИКА"]


def test_search_region_confirmed_delivery_beats_office_elsewhere(conn):
    ids = seed(conn)
    r = search(conn, "сыр Екатеринбург")
    fits = {h.card.id: h.region_fit for h in r.hits}
    assert fits[ids["СЫРОВАРНЯ УРАЛ"]] == "office"
    assert fits[ids["МОЛОКО ПЛЮС"]] == "confirmed"
    assert all(h.region_fit != "unknown" for h in r.in_region)
    only = search(conn, "шоколад", Filters(region="Свердловская область", only_region=True))
    assert only.hits == []


def test_search_empty_logs_discovery(conn):
    seed(conn)
    r = search(conn, "несуществующийтовар")
    assert r.hits == [] and r.unmatched == ["несуществующийтовар"] and r.discovery_logged
    assert conn.execute("SELECT count(*) AS n FROM discovery_requests").fetchone()["n"] == 1


def test_seed_roundtrip(conn, tmp_path, monkeypatch):
    from goulash import cli

    seed(conn)
    monkeypatch.setattr(cli, "SEED_FILE", tmp_path / "seed.jsonl.gz")
    cli.cmd_export_seed(None, conn)
    conn.execute("TRUNCATE suppliers CASCADE")
    conn.commit()
    assert cli.load_seed(conn, tmp_path / "seed.jsonl.gz") == 4
    assert search(conn, "моцарелла").hits[0].card.name == "СЫРОВАРНЯ УРАЛ"


def test_web_pages(conn, db_url):
    from fastapi.testclient import TestClient

    from goulash.web.app import app

    ids = seed(conn)
    sid = ids["СЫРОВАРНЯ УРАЛ"]
    with TestClient(app) as client:
        for url in [
            "/",
            "/search?q=сыр",
            "/search?q=&region=Москва и Московская область",
            f"/supplier/{sid}",
            f"/compare?ids={sid},{ids['МОЛОКО ПЛЮС']}",
            "/shortlist",
            "/about",
            "/healthz",
        ]:
            assert client.get(url).status_code == 200, url
        assert client.get("/supplier/999999").status_code == 404
        # a place typed in the query becomes the region filter and wins over a stale select value
        page = client.get("/search", params={"q": "сыр екатеринбург", "region": "Москва и Московская область"}).text
        assert "Регион: Свердловская область (из запроса «екатеринбург»)" in page and 'value="сыр"' in page
        page = client.get("/search", params={"q": "сыр", "sort": "name"}).text
        assert page.index("МОЛОКО ПЛЮС") < page.index("СЫРОВАРНЯ УРАЛ")
        client.post(f"/supplier/{sid}/notes", data={"text": "Позвонить во вторник"})
        client.post(f"/supplier/{sid}/shortlist", data={"status": "to_contact"})
        page = client.get(f"/supplier/{sid}").text
        assert "Позвонить во вторник" in page
        assert "СЫРОВАРНЯ УРАЛ" in client.get("/shortlist").text
        api = client.get("/api/search", params={"q": "моцарела"}).json()
        assert api["results"][0]["name"] == "СЫРОВАРНЯ УРАЛ"
