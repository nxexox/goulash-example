"""Read model for the UI/API: supplier cards with contacts, facts, completeness and call questions.

Persistence of user data (notes, shortlist) lives here too, so storage can be swapped in one place.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from goulash.taxonomy import BY_ID

# field -> (title, question to ask when unknown)
DECISION_FIELDS = {
    "moq": ("Минимальный заказ", "Какой минимальный объём или сумма заказа?"),
    "price": ("Цена", "Есть ли актуальный прайс для вашего объёма?"),
    "certificates": ("Документы и сертификаты", "Какие документы предоставляете (декларации, ВСД, ХАССП)?"),
    "delivery": ("Условия доставки", "Как доставляете и сколько стоит доставка в ваш город?"),
    "delivery_regions": ("Регионы доставки", "Возите ли в ваш регион и с какой периодичностью?"),
}
SHORTLIST_STATUSES = {
    "to_contact": "Связаться",
    "contacted": "Связались",
    "negotiating": "Переговоры",
    "rejected": "Не подходит",
}
SOURCE_TITLES = {"prodexpo": "Продэкспо-2026", "worldfood": "WorldFood 2026", "fns_rsmp": "Реестр МСП ФНС"}


@dataclass
class SupplierCard:
    id: int
    name: str
    website: str | None
    domain: str | None
    city: str | None
    region: str | None
    country: str | None
    address: str | None
    description: str
    categories: list[str]
    rubrics: list[str]
    products: list[str]
    first_seen: datetime
    last_seen: datetime
    enriched_at: datetime | None
    phones: list[dict] = field(default_factory=list)
    emails: list[dict] = field(default_factory=list)
    facts: dict[str, list[dict]] = field(default_factory=dict)
    sources: list[dict] = field(default_factory=list)
    shortlist: str | None = None
    notes: list[dict] = field(default_factory=list)

    @property
    def category_titles(self) -> list[str]:
        return [BY_ID[c].title for c in self.categories if c in BY_ID]

    @property
    def delivery_regions(self) -> list[str]:
        return [f["value"] for f in self.facts.get("delivery_regions", [])]

    @property
    def nationwide(self) -> bool:
        return any(f["value"] == "по всей России" for f in self.facts.get("delivery", []))

    @property
    def completeness(self) -> tuple[int, int]:
        items = self.data_items
        return sum(known for _, known in items), len(items)

    @property
    def data_items(self) -> list[tuple[str, bool]]:
        """What we know about the supplier, field by field: explains the "Данные: 5 из 9" score."""
        return [
            ("Сайт", bool(self.website)),
            ("Телефон", bool(self.phones)),
            ("E-mail", bool(self.emails)),
            ("Адрес", bool(self.address or self.city)),
            *((title, bool(self.facts.get(k))) for k, (title, _) in DECISION_FIELDS.items()),
        ]

    @property
    def questions(self) -> list[str]:
        return [q for k, (_, q) in DECISION_FIELDS.items() if not self.facts.get(k)]

    @property
    def age_days(self) -> int:
        return (datetime.now(UTC) - self.last_seen).days

    def region_fit(self, region: str | None) -> str | None:
        """confirmed | nationwide | office | unknown — office region is a weaker signal than delivery."""
        if not region:
            return None
        if region in self.delivery_regions:
            return "confirmed"
        if self.nationwide:
            return "nationwide"
        if self.region == region:
            return "office"
        return "unknown"


def load_cards(conn, ids: list[int]) -> list[SupplierCard]:
    if not ids:
        return []
    rows = conn.execute(
        """SELECT id, name, website, domain, city, region, country, address, coalesce(description, '') AS description,
                  categories, rubrics, products, first_seen, last_seen, enriched_at
           FROM suppliers WHERE id = ANY(%s)""",
        (ids,),
    ).fetchall()
    cards = {r["id"]: SupplierCard(**r) for r in rows}
    for c in conn.execute(
        "SELECT supplier_id, kind, value, source_url, observed_at FROM contacts WHERE supplier_id = ANY(%s) "
        "ORDER BY observed_at",
        (ids,),
    ).fetchall():
        target = cards[c["supplier_id"]].phones if c["kind"] == "phone" else cards[c["supplier_id"]].emails
        target.append(c)
    for f in conn.execute(
        """SELECT supplier_id, field, value, evidence, source_url, observed_at, method, verification
           FROM facts WHERE supplier_id = ANY(%s) ORDER BY field, value""",
        (ids,),
    ).fetchall():
        cards[f["supplier_id"]].facts.setdefault(f["field"], []).append(f)
    for o in conn.execute(
        """SELECT supplier_id, source, edition, source_url, observed_at FROM observations
           WHERE supplier_id = ANY(%s) ORDER BY observed_at DESC""",
        (ids,),
    ).fetchall():
        o["title"] = SOURCE_TITLES.get(o["source"], o["source"])
        cards[o["supplier_id"]].sources.append(o)
    for s in conn.execute("SELECT supplier_id, status FROM shortlist WHERE supplier_id = ANY(%s)", (ids,)).fetchall():
        cards[s["supplier_id"]].shortlist = s["status"]
    for n in conn.execute(
        "SELECT id, supplier_id, text, created_at FROM notes WHERE supplier_id = ANY(%s) ORDER BY created_at DESC",
        (ids,),
    ).fetchall():
        cards[n["supplier_id"]].notes.append(n)
    return [cards[i] for i in ids if i in cards]


def add_note(conn, supplier_id: int, text: str) -> None:
    conn.execute("INSERT INTO notes (supplier_id, text) VALUES (%s, %s)", (supplier_id, text.strip()[:2000]))
    conn.commit()


def delete_note(conn, note_id: int) -> None:
    conn.execute("DELETE FROM notes WHERE id = %s", (note_id,))
    conn.commit()


def set_shortlist(conn, supplier_id: int, status: str | None) -> None:
    if status in SHORTLIST_STATUSES:
        conn.execute(
            """INSERT INTO shortlist (supplier_id, status) VALUES (%s, %s)
               ON CONFLICT (supplier_id) DO UPDATE SET status = excluded.status, updated_at = now()""",
            (supplier_id, status),
        )
    else:
        conn.execute("DELETE FROM shortlist WHERE supplier_id = %s", (supplier_id,))
    conn.commit()


def shortlist_ids(conn) -> list[int]:
    return [
        r["supplier_id"] for r in conn.execute("SELECT supplier_id FROM shortlist ORDER BY updated_at DESC").fetchall()
    ]


def catalog_stats(conn) -> dict:
    return conn.execute(
        """SELECT count(*) AS suppliers,
                  count(*) FILTER (WHERE enriched_at IS NOT NULL) AS enriched,
                  (SELECT count(DISTINCT region) FROM suppliers) AS regions,
                  (SELECT count(*) FROM facts) AS facts,
                  max(last_seen) AS updated
           FROM suppliers"""
    ).fetchone()


def category_counts(conn) -> list[tuple[str, str, int]]:
    rows = conn.execute(
        "SELECT c, count(*) AS n FROM suppliers, unnest(categories) AS c GROUP BY c ORDER BY n DESC"
    ).fetchall()
    return [(r["c"], BY_ID[r["c"]].title, r["n"]) for r in rows if r["c"] in BY_ID]


def region_counts(conn) -> list[tuple[str, int]]:
    rows = conn.execute(
        "SELECT region, count(*) AS n FROM suppliers WHERE region IS NOT NULL GROUP BY region ORDER BY n DESC"
    ).fetchall()
    return [(r["region"], r["n"]) for r in rows]
