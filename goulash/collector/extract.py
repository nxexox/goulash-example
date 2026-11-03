"""Rule-based fact extraction from supplier pages and catalog descriptions (CPU only, no LLM).

Every fact keeps the line it came from. Commercial conditions are stored as *evidence-backed hints*:
"минимальная сумма заказа 5 000 ₽" is a fact with a source; a bare "₽" on a page is not.
"""

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from goulash import geo
from goulash.collector.html import Page, jsonld_nodes
from goulash.text import words

PARSER_VERSION = "site-v2"

PHONE_RE = re.compile(r"(?:\+7|\b8)[\s\-(]*\d{3,5}[\s\-)]*\d{1,3}[\s\-]?\d{2}[\s\-]?\d{2}\b")
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}\b", re.I)
INN_RE = re.compile(r"ИНН\s*[:№]?\s*(\d{10}|\d{12})\b")
NUM = r"(\d[\d\s]{0,9}(?:[.,]\d+)?)"
MOQ_RE = re.compile(
    r"(минимальн\w*\s+(?:сумм\w*|объ[её]м\w*|парти\w*|заказ\w*)(?:\s+заказ\w*)?|заказ\s+от|от)\s*[:—–-]?\s*"
    + NUM
    + r"\s*(₽|руб\w*|р\.|тыс\.?\s*(?:₽|руб\w*)|кг|тонн\w*|т\b|шт\w*|короб\w*|упаков\w*|паллет\w*)",
    re.I,
)
PRICE_RE = re.compile(r"(от\s*)?" + NUM + r"\s*(₽|руб\.?|р\.)\s*(/|за)\s*(кг|шт|л|уп\w*|короб\w*|т\b)", re.I)
CERTS = {
    "ISO 22000": r"iso\s*22000",
    "FSSC 22000": r"fssc\s*22000",
    "ISO 9001": r"iso\s*9001",
    "HACCP / ХАССП": r"haccp|хассп|хаccп",
    "ГОСТ": r"\bгост\b",
    "Халяль": r"халял",
    "Кошер": r"кошер",
    "Декларация/сертификат соответствия": r"деклараци\w+ о соответствии|сертификат\w* соответствия|ЕАЭС",
    "Меркурий (ВСД)": r"меркури|\bвсд\b",
    "Честный знак": r"честный знак",
    "Органик": r"органическ\w+ (?:сертиф|продукци)|organic certif|сертифицирован\w* органическ",
}
DELIVERY_SCOPE = {
    "по всей России": r"по\s+(?:всей\s+)?(?:территории\s+)?росси|во\s+все\s+регион|по\s+рф\b",
    "СНГ / ЕАЭС": r"\bснг\b|ЕАЭС|ближне\w* зарубеж",
    "Самовывоз": r"самовывоз",
    "Транспортные компании": r"транспортн\w+ компани|\bтк\b|деловые линии|пэк|сдэк",
}
SUBPAGE_RE = re.compile(
    r"contact|kontakt|контакт|dostav|deliver|доставк|oplat|оплат|about|o-kompan|о-компан|"
    r"partner|dealer|сертиф|sertif|price|прайс|opt|оптов",
    re.I,
)


FREE_DELIVERY_RE = re.compile(r"(?:бесплатн\w+\s+доставк\w*|доставк\w*\s+бесплатн\w*)[^.;]{0,40}?\bот\s*$", re.I)
DELIVERY_VERB = re.compile(r"доставк|доставля|привоз|развоз|отгружа\w+ в", re.I)
THIRD_PARTY = re.compile(r"партн[её]р|разработ|сайт создан|продвижени|оквэд\s*(?:58|62|63)", re.I)
PRICE_NOISE = re.compile(r"подробнее|в корзину|купить|заказать|руб\w*|₽|[\d\s.,/]|\b(?:за|от|кг|шт|л|уп)\b", re.I)


@dataclass
class Fact:
    field: str
    value: str
    evidence: str
    source_url: str
    method: str = "regex"


@dataclass
class SiteExtraction:
    url: str
    phones: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)
    product_names: list[str] = field(default_factory=list)
    subpages: list[str] = field(default_factory=list)


def inn_valid(inn: str) -> bool:
    d = [int(c) for c in inn]

    def check(weights):
        return sum(w * x for w, x in zip(weights, d, strict=False)) % 11 % 10

    if len(d) == 10:
        return check([2, 4, 10, 3, 5, 9, 4, 6, 8]) == d[9]
    if len(d) == 12:
        return check([7, 2, 4, 10, 3, 5, 9, 4, 6, 8]) == d[10] and check([3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]) == d[11]
    return False


def normalize_phone(raw: str) -> str | None:
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits[0] in "78":
        digits = "7" + digits[1:]
        return f"+7 ({digits[1:4]}) {digits[4:7]}-{digits[7:9]}-{digits[9:]}"
    return None


def _short(line: str, limit: int = 240) -> str:
    return line if len(line) <= limit else line[: limit - 1] + "…"


def delivery_regions(line: str) -> list[str]:
    """Regions from clauses that talk about delivery and name a place after "по/в/до":
    "доставка по Екатеринбургу" -> yes; "склад в Санкт-Петербурге", "до 16:00 по Москве" -> no."""
    out: dict[str, None] = {}
    for clause in re.split(r"[.;!?]|\s[—–-]\s", line):
        if not DELIVERY_VERB.search(clause) or re.search(r"\d{1,2}:\d{2}\s*(?:по|мск)", clause, re.I):
            continue
        toks = words(clause)
        last_end = -1
        for city, start, end in geo.find_cities(clause):
            prev = toks[start - 1] if start else ""
            delivery_before = any(t.startswith(("достав", "привоз", "развоз", "отгруж")) for t in toks[:start])
            listed = last_end >= 0 and (start == last_end or (prev == "и" and start == last_end + 1))
            if delivery_before and (prev in ("по", "в", "во", "до") or listed) and (region := geo.region_of_city(city)):
                out[region] = None
                last_end = end
    return list(out)[:10]


def text_facts(lines: list[str], source_url: str) -> list[Fact]:
    """Facts that can appear in any free text: certificates, MOQ, prices, delivery scope, INN."""
    facts: list[Fact] = []
    seen: set[tuple[str, str]] = set()

    def add(fieldname: str, value: str, evidence: str):
        if (fieldname, value) not in seen:
            seen.add((fieldname, value))
            facts.append(Fact(fieldname, value, _short(evidence), source_url))

    for line in lines:
        if len(line) > 600:  # long blobs are usually menus/footers glued together
            continue
        low = line.lower()
        for name, rx in CERTS.items():
            if re.search(rx, line, re.I):
                add("certificates", name, line)
        if "миним" in low or "заказ от" in low:
            for m in MOQ_RE.finditer(line):
                qty = f"{re.sub(r'\s+', ' ', m.group(2)).strip()} {m.group(3).strip()}"
                if FREE_DELIVERY_RE.search(line[: m.start(2)]):
                    # free-delivery threshold is a delivery condition, not a minimum order
                    add("delivery", f"Бесплатная доставка от {qty}", line)
                    continue
                if m.group(1).lower() == "от" and "заказ" not in line[max(0, m.start() - 25) : m.start()].lower():
                    continue
                add("moq", qty, line)
        # a price is only useful next to a product name: "100 ₽/кг Подробнее" alone is skipped
        has_product = len(PRICE_NOISE.sub("", line).strip()) >= 6
        for m in PRICE_RE.finditer(line if has_product else ""):
            value = f"{'от ' if m.group(1) else ''}{re.sub(r'\s+', ' ', m.group(2)).strip()} ₽/{m.group(5)}"
            add("price", value, line)
        if re.search(r"доставк|достав[ля]ем|отгрузк", low):
            for name, rx in DELIVERY_SCOPE.items():
                if re.search(rx, line, re.I):
                    add("delivery", name, line)
            for region in delivery_regions(line):
                add("delivery_regions", region, line)
            if len(line) < 300 and not any(f.field == "delivery_note" for f in facts):
                add("delivery_note", _short(line, 200), line)
        for m in INN_RE.finditer(line):
            if inn_valid(m.group(1)) and not THIRD_PARTY.search(line):
                add("inn", m.group(1), line)
    return facts


def extract_site(page: Page) -> SiteExtraction:
    out = SiteExtraction(url=page.url)
    host = (urlsplit(page.url).hostname or "").removeprefix("www.")
    phones: dict[str, None] = {}
    emails: dict[str, None] = {}
    for link in page.links:
        if link.startswith("tel:"):
            if p := normalize_phone(link[4:]):
                phones[p] = None
        elif link.startswith("mailto:"):
            e = link[7:].split("?")[0].strip().lower()
            if EMAIL_RE.fullmatch(e):
                emails[e] = None
        elif (urlsplit(link).hostname or "").removeprefix("www.") == host and SUBPAGE_RE.search(link):
            out.subpages.append(link.split("#")[0])
    for m in PHONE_RE.finditer(page.text):
        if p := normalize_phone(m.group(0)):
            phones[p] = None
    for m in EMAIL_RE.finditer(page.text):
        e = m.group(0).lower()
        if not e.endswith((".png", ".jpg", ".webp", ".svg")):
            emails[e] = None
    out.phones = list(phones)[:6]
    out.emails = list(emails)[:6]
    out.facts = text_facts(page.lines, page.url)
    for block in page.jsonld:
        for node in jsonld_nodes(block):
            types = node.get("@type")
            types = types if isinstance(types, list) else [types]
            if "Product" in types and isinstance(node.get("name"), str):
                out.product_names.append(node["name"][:120])
            if any(t in ("Organization", "LocalBusiness") for t in types):
                tel = node.get("telephone")
                if isinstance(tel, str) and (p := normalize_phone(tel)):
                    out.phones = list(dict.fromkeys([*out.phones, p]))[:6]
    out.subpages = list(dict.fromkeys(out.subpages))[:4]
    return out
