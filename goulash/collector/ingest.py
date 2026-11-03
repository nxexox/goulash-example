"""Idempotent writes: observations (raw), suppliers (merged), contacts, facts, search vector."""

import hashlib
import json
import re
from urllib.parse import urlsplit

import psycopg

from goulash import geo
from goulash.collector.adapters.base import CatalogRecord
from goulash.collector.extract import PARSER_VERSION, Fact, SiteExtraction, normalize_phone, text_facts
from goulash.taxonomy import BY_ID, categories_for_text
from goulash.text import stemmed_text

CATALOG_PARSER = "catalog-v2"
# Hosts that are never a supplier's own site.
NOT_SUPPLIER_HOSTS = re.compile(
    r"(^|\.)(vk\.com|t\.me|telegram\.org|instagram\.com|facebook\.com|youtube\.com|ok\.ru|wa\.me|"
    r"expocentr\.ru|prod-expo\.ru|world-food\.ru|exhibitoronlinemanual\.com|google\.\w+|yandex\.\w+)$"
)


def domain_of(url: str | None) -> str | None:
    if not url:
        return None
    host = (urlsplit(url if "//" in url else "http://" + url).hostname or "").lower().removeprefix("www.")
    if not host or "." not in host or NOT_SUPPLIER_HOSTS.search(host):
        return None
    return host


def _hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def upsert_record(conn: psycopg.Connection, rec: CatalogRecord, observed_at: str | None = None) -> tuple[int, str]:
    """Returns (supplier_id, state) where state is inserted | updated | unchanged."""
    payload = rec.to_dict()
    digest = _hash(payload)
    obs = conn.execute(
        "SELECT payload_hash, supplier_id FROM observations WHERE source=%s AND edition=%s AND external_id=%s",
        (rec.source, rec.edition, rec.external_id),
    ).fetchone()
    if obs and obs["payload_hash"] == digest and obs["supplier_id"]:
        conn.execute(
            "UPDATE suppliers SET last_seen = now() WHERE id = %s", (obs["supplier_id"],)
        )  # seen again: freshness, no fact churn
        return obs["supplier_id"], "unchanged"

    domain = domain_of(rec.website)
    key = domain or f"{rec.source}:{rec.external_id}"
    city = rec.city or geo.city_from_address(rec.address)
    region = geo.region_of_city(city) or geo.region_from_address(rec.address)
    cat_text = " \n".join([rec.name, *rec.rubrics, *rec.products]) or rec.description
    categories = categories_for_text(cat_text) or categories_for_text(rec.description)

    row = conn.execute(
        """
        INSERT INTO suppliers (key, name, website, domain, city, region, country, address, description,
                               categories, rubrics, products, first_seen, last_seen)
        VALUES (%(key)s, %(name)s, %(website)s, %(domain)s, %(city)s, %(region)s, %(country)s, %(address)s,
                %(description)s, %(categories)s, %(rubrics)s, %(products)s,
                coalesce(%(observed)s::timestamptz, now()), coalesce(%(observed)s::timestamptz, now()))
        ON CONFLICT (key) DO UPDATE SET
            name = CASE WHEN length(excluded.name) > 0 THEN excluded.name ELSE suppliers.name END,
            website = coalesce(excluded.website, suppliers.website),
            city = coalesce(excluded.city, suppliers.city),
            region = coalesce(excluded.region, suppliers.region),
            country = coalesce(excluded.country, suppliers.country),
            address = coalesce(excluded.address, suppliers.address),
            description = CASE WHEN length(excluded.description) > length(coalesce(suppliers.description, ''))
                               THEN excluded.description ELSE suppliers.description END,
            categories = ARRAY(SELECT DISTINCT unnest(suppliers.categories || excluded.categories)),
            rubrics = ARRAY(SELECT DISTINCT unnest(suppliers.rubrics || excluded.rubrics)),
            products = ARRAY(SELECT DISTINCT unnest(suppliers.products || excluded.products)),
            last_seen = excluded.last_seen
        RETURNING id, (xmax = 0) AS inserted
        """,
        dict(
            key=key,
            name=rec.name.strip(),
            website=rec.website,
            domain=domain,
            city=city,
            region=region,
            country=rec.country,
            address=rec.address,
            description=rec.description,
            categories=categories,
            rubrics=rec.rubrics,
            products=rec.products,
            observed=observed_at,
        ),
    ).fetchone()
    sid = row["id"]
    for p in rec.phones:
        if norm := normalize_phone(p):
            add_contact(conn, sid, "phone", norm, rec.source_url)
    for e in rec.emails:
        add_contact(conn, sid, "email", e.lower(), rec.source_url)
    facts = text_facts(rec.description.splitlines(), rec.source_url)
    facts += [
        Fact("certificates", "Халяль", r, rec.source_url, "catalog_field") for r in rec.rubrics if "халял" in r.lower()
    ]
    for f in facts:
        add_fact(conn, sid, f, None, CATALOG_PARSER)
    conn.execute(
        """
        INSERT INTO observations (source, edition, external_id, source_url, payload_hash, payload, supplier_id,
                                  observed_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, coalesce(%s::timestamptz, now()))
        ON CONFLICT (source, edition, external_id) DO UPDATE SET
            payload_hash = excluded.payload_hash, payload = excluded.payload, supplier_id = excluded.supplier_id,
            source_url = excluded.source_url, observed_at = excluded.observed_at
        """,
        (
            rec.source,
            rec.edition,
            rec.external_id,
            rec.source_url,
            digest,
            json.dumps(payload, ensure_ascii=False),
            sid,
            observed_at,
        ),
    )
    refresh_search_vector(conn, sid)
    return sid, "inserted" if row["inserted"] else "updated"


def add_contact(conn, supplier_id: int, kind: str, value: str, source_url: str) -> None:
    conn.execute(
        """INSERT INTO contacts (supplier_id, kind, value, source_url, observed_at) VALUES (%s, %s, %s, %s, now())
           ON CONFLICT (supplier_id, kind, value) DO UPDATE SET observed_at = now()""",
        (supplier_id, kind, value, source_url),
    )


def add_fact(conn, supplier_id: int, f: Fact, snapshot_hash: str | None, parser_version: str) -> None:
    conn.execute(
        """
        INSERT INTO facts (supplier_id, field, value, evidence, source_url, snapshot_hash, observed_at,
                           parser_version, method)
        VALUES (%s, %s, %s, %s, %s, %s, now(), %s, %s)
        ON CONFLICT (supplier_id, field, value) DO UPDATE SET
            evidence = excluded.evidence, source_url = excluded.source_url, snapshot_hash = excluded.snapshot_hash,
            observed_at = now(), parser_version = excluded.parser_version, method = excluded.method
        """,
        (supplier_id, f.field, f.value, f.evidence, f.source_url, snapshot_hash, parser_version, f.method),
    )


def apply_site(conn, supplier_id: int, pages: list[tuple[SiteExtraction, str]]) -> int:
    """Merge supplier-site extraction. Facts from earlier site parses are replaced (not accumulated forever),
    catalog facts stay. Returns number of facts written."""
    conn.execute("DELETE FROM facts WHERE supplier_id = %s AND parser_version = %s", (supplier_id, PARSER_VERSION))
    n = 0
    products: list[str] = []
    for ext, snapshot in pages:
        for p in ext.phones:
            add_contact(conn, supplier_id, "phone", p, ext.url)
        for e in ext.emails:
            add_contact(conn, supplier_id, "email", e, ext.url)
        for f in ext.facts:
            add_fact(conn, supplier_id, f, snapshot, PARSER_VERSION)
            n += 1
        products += ext.product_names
    conn.execute(
        """UPDATE suppliers SET enriched_at = now(), status = 'enriched',
                  products = ARRAY(SELECT DISTINCT unnest(products || %s::text[])) WHERE id = %s""",
        (products[:50], supplier_id),
    )
    refresh_search_vector(conn, supplier_id)
    return n


def refresh_search_vector(conn, supplier_id: int) -> None:
    s = conn.execute(
        "SELECT name, description, categories, rubrics, products FROM suppliers WHERE id = %s", (supplier_id,)
    ).fetchone()
    cat_titles = " ".join(BY_ID[c].title for c in s["categories"] if c in BY_ID)
    parts = {
        "A": stemmed_text(s["name"]),
        "B": stemmed_text(" ".join([cat_titles, *s["rubrics"]])),
        "C": stemmed_text(" ".join(s["products"])),
        "D": stemmed_text((s["description"] or "")[:4000]),
    }
    conn.execute(
        """UPDATE suppliers SET search_vector =
               setweight(to_tsvector('simple', %(A)s), 'A') || setweight(to_tsvector('simple', %(B)s), 'B') ||
               setweight(to_tsvector('simple', %(C)s), 'C') || setweight(to_tsvector('simple', %(D)s), 'D')
           WHERE id = %(id)s""",
        {**parts, "id": supplier_id},
    )


def rebuild_vocabulary(conn) -> int:
    with conn.transaction():
        conn.execute("DELETE FROM vocabulary")
        conn.execute(
            """INSERT INTO vocabulary (term, freq)
               SELECT word, ndoc FROM ts_stat('SELECT search_vector FROM suppliers WHERE search_vector IS NOT NULL')
               WHERE length(word) >= 3 AND word !~ '^[0-9]+$'"""
        )
    return conn.execute("SELECT count(*) AS n FROM vocabulary").fetchone()["n"]
