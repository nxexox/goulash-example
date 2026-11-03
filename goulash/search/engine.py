"""Search over the prepared catalog: PostgreSQL FTS + pg_trgm typo correction + rule-based ranking.

Retrieval order (PoC/PLAN.md §8): exact term match -> equivalent aliases -> typo fallback -> category-level match.
Category-level matches are labelled: "в категории «Сыры»" is not proof the supplier has mozzarella.
"""

import math
import re
from dataclasses import dataclass, field

from goulash import config
from goulash.repo import SupplierCard, load_cards
from goulash.search.query import ParsedQuery, parse
from goulash.taxonomy import BY_ID, categories_for_stem, equivalent_stems

TYPO_SIMILARITY = 0.45


@dataclass
class Filters:
    region: str | None = None  # explicit region select; overrides region parsed from query
    category: str | None = None
    only_region: bool = False  # hide suppliers without any link to the region
    has_contacts: bool = False
    has_conditions: bool = False  # any of moq / price / delivery known


@dataclass
class Hit:
    card: SupplierCard
    score: float
    exact: bool
    reasons: list[str]
    region_fit: str | None


@dataclass
class SearchResult:
    query: ParsedQuery
    region: str | None
    hits: list[Hit] = field(default_factory=list)
    corrections: list[tuple[str, str]] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)
    relaxed: bool = False
    discovery_logged: bool = False

    @property
    def in_region(self) -> list[Hit]:
        return [h for h in self.hits if h.region_fit in (None, "confirmed", "nationwide", "office")]

    @property
    def out_of_region(self) -> list[Hit]:
        return [h for h in self.hits if h.region_fit == "unknown"]


def _known(conn, stem: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM vocabulary WHERE term LIKE %s LIMIT 1", (stem + "%",)).fetchone())


def _correct(conn, stem: str) -> str | None:
    row = conn.execute(
        """SELECT term, similarity(term, %s) AS sim FROM vocabulary
           WHERE term %% %s ORDER BY sim DESC, freq DESC LIMIT 1""",
        (stem, stem),
    ).fetchone()
    return row["term"] if row and row["sim"] >= TYPO_SIMILARITY else None


def _lexeme(s: str) -> str:
    return re.sub(r"[^\w]", "", s)


def _tsquery(groups: list[set[str]], mode: str) -> str:
    parts = ["(" + " | ".join(f"{_lexeme(s)}:*" for s in sorted(g) if _lexeme(s)) + ")" for g in groups]
    return f" {mode} ".join(p for p in parts if p != "()")


def search(conn, raw: str, filters: Filters | None = None, limit: int = 40) -> SearchResult:
    filters = filters or Filters()
    q = parse(raw)
    region = filters.region or q.region
    res = SearchResult(query=q, region=region)

    groups: list[set[str]] = []
    labels: list[str] = []  # what we actually searched for, shown in "why this result"
    for t in q.terms:
        labels.append(t.word)
        g = equivalent_stems(t.stem)
        if not any(_known(conn, s) for s in g):
            fixed = _correct(conn, t.stem)
            if fixed:
                res.corrections.append((t.word, fixed))
                labels[-1] = fixed + "…"
                g = g | equivalent_stems(fixed)
                t.categories |= categories_for_stem(fixed)
            elif not t.categories:
                res.unmatched.append(t.word)
        groups.append(g)

    cat_ids = set(q.category_ids)
    if filters.category:
        cat_ids.add(filters.category)

    if not groups and not cat_ids and not region:
        return res

    rows = _retrieve(conn, groups, "&", cat_ids, filters, region)
    if not rows and len(groups) > 1:
        rows = _retrieve(conn, groups, "|", cat_ids, filters, region)
        res.relaxed = bool(rows)

    cards = {c.id: c for c in load_cards(conn, [r["id"] for r in rows])}
    max_rank = max((r["rank"] for r in rows), default=0) or 1
    for r in rows:
        card = cards[r["id"]]
        hit = _score(card, r, labels, res.relaxed, cat_ids, region, max_rank)
        if filters.only_region and region and hit.region_fit == "unknown":
            continue
        if filters.has_contacts and not (card.phones or card.emails):
            continue
        if filters.has_conditions and not any(card.facts.get(k) for k in ("moq", "price", "delivery")):
            continue
        res.hits.append(hit)
    res.hits.sort(key=lambda h: -h.score)
    res.hits = res.hits[:limit]

    if len(res.hits) < config.DISCOVERY_THRESHOLD and q.raw:
        conn.execute(
            "INSERT INTO discovery_requests (query, region, results) VALUES (%s, %s, %s)",
            (q.raw, region, len(res.hits)),
        )
        conn.commit()
        res.discovery_logged = True
    return res


def _retrieve(conn, groups, mode, cat_ids, filters: Filters, region):
    tsq = _tsquery(groups, mode) if groups else ""
    where, params = [], {"tsq": tsq or None, "cats": list(cat_ids), "region": region}
    match = []
    if tsq:
        match.append("search_vector @@ to_tsquery('simple', %(tsq)s)")
    if cat_ids and (not groups or mode == "|" or all(_group_has_category(g) for g in groups)):
        match.append("categories && %(cats)s::text[]")
    if match:
        where.append("(" + " OR ".join(match) + ")")
    if filters.category:
        where.append("%(cat)s = ANY(categories)")
        params["cat"] = filters.category
    if not where and region:
        where.append("""(region = %(region)s OR EXISTS (SELECT 1 FROM facts f WHERE f.supplier_id = suppliers.id
                         AND f.field = 'delivery_regions' AND f.value = %(region)s))""")
    rank = "ts_rank_cd(search_vector, to_tsquery('simple', %(tsq)s), 1)" if tsq else "0"
    exact = "search_vector @@ to_tsquery('simple', %(tsq)s)" if tsq else "false"
    sql = f"""SELECT id, {rank} AS rank, {exact} AS exact, categories FROM suppliers
              WHERE {" AND ".join(where) or "true"} LIMIT 400"""
    return conn.execute(sql, params).fetchall()


def _group_has_category(g: set[str]) -> bool:
    return any(categories_for_stem(s) for s in g)


def _score(card: SupplierCard, row, labels: list[str], relaxed: bool, cat_ids: set[str], region, max_rank) -> Hit:
    reasons: list[str] = []
    score = 0.0
    if row["exact"]:
        score += 100 + 30 * (row["rank"] / max_rank)
        if labels:
            prefix = "Частичное совпадение, искали: " if relaxed else "Совпадение по товару: "
            reasons.append(prefix + ", ".join(labels))
    shared = [BY_ID[c].title for c in cat_ids if c in card.categories]
    if shared:
        score += 40
        reasons.append(
            ("Категория: " if row["exact"] else "Только по категории (товар не подтверждён): ") + ", ".join(shared)
        )

    fit = card.region_fit(region)
    if fit == "confirmed":
        score += 45
        reasons.append(f"Доставка в регион «{region}» указана на сайте")
    elif fit == "nationwide":
        score += 30
        reasons.append("Доставка «по всей России» указана на сайте")
    elif fit == "office":
        score += 25
        reasons.append(f"Офис/производство в регионе ({card.city})")

    if row["exact"] and 0 < len(card.categories) <= 3:
        score += 12  # a specialist usually beats a broad distributor for a specific product
        reasons.append("Профильный поставщик")
    elif row["exact"] and len(card.categories) <= 6:
        score += 6

    filled, total = card.completeness
    score += 15 * filled / total
    if card.facts.get("moq") or card.facts.get("price"):
        score += 5
        reasons.append("Есть коммерческие условия")
    if card.facts.get("certificates"):
        reasons.append("Документы: " + ", ".join(f["value"] for f in card.facts["certificates"][:3]))
    score -= min(10, math.log1p(card.age_days))  # mild freshness decay
    return Hit(card=card, score=round(score, 2), exact=bool(row["exact"]), reasons=reasons, region_fit=fit)
