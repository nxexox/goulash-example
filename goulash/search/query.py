"""Deterministic query parsing: product terms, delivery city/region, quantities. No LLM."""

import re
from dataclasses import dataclass, field

from goulash import geo
from goulash.taxonomy import categories_for_stem
from goulash.text import STOPWORDS, normalize, stem, words

QTY_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(кг|г|гр|т|тонн\w*|л|мл|шт|уп|короб\w*|паллет\w*|₽|руб\w*|тыс\w*)\b", re.I)
UNIT = {"гр": "г", "тонн": "т", "руб": "₽"}


@dataclass
class Term:
    word: str
    stem: str
    categories: set[str] = field(default_factory=set)


@dataclass
class ParsedQuery:
    raw: str
    terms: list[Term] = field(default_factory=list)
    city: str | None = None
    region: str | None = None
    quantities: list[str] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)

    @property
    def category_ids(self) -> set[str]:
        return {c for t in self.terms for c in t.categories}


def parse(raw: str) -> ParsedQuery:
    q = ParsedQuery(raw=raw.strip())
    text = normalize(raw)
    for m in QTY_RE.finditer(text):
        unit = next((v for k, v in UNIT.items() if m.group(2).startswith(k)), m.group(2))
        q.quantities.append(f"{m.group(1)} {unit}")
    text_wo_qty = QTY_RE.sub(" ", text)

    toks = words(text_wo_qty)
    city_spans = geo.find_cities(text_wo_qty)
    region_spans = geo.find_regions(text_wo_qty)
    covered: set[int] = set()
    if city_spans:
        q.city = city_spans[0][0]
        q.region = geo.region_of_city(q.city)
    elif region_spans:
        q.region = region_spans[0][0]
    for _, a, b in city_spans + region_spans:
        covered.update(range(a, b))
    # "область"/"край" next to a region name are part of the place, not the product
    covered.update(i for i, w in enumerate(toks) if w in {"область", "обл", "край", "республика"})
    for i, w in enumerate(toks):
        if i in covered or w in STOPWORDS or len(w) < 2 or w.isdigit():
            continue
        s = stem(w)
        q.terms.append(Term(word=w, stem=s, categories=categories_for_stem(s)))

    if q.quantities:
        q.hints.append(
            f"«{', '.join(q.quantities)}» — фасовка или объём заказа? Поставщики редко публикуют это; "
            "уточните при звонке. Количество не используется как фильтр."
        )
    if q.region:
        q.hints.append(
            f"Регион доставки: {q.region}. Город офиса и зона доставки — разные вещи, "
            "показываем отдельно подтверждённые и неподтверждённые."
        )
    return q
