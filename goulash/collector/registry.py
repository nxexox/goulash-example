"""FNS SME registry (open data, nalog.gov.ru/opendata/7707329152-rsmp): the backbone list of food companies.

The registry gives a legal entity with INN, region and OKVED activity codes, but no contacts. A company becomes a
supplier only after its own site is found and verified (see discovery.py). Individual entrepreneurs (ИП) are
skipped: their record is a personal name, not a company.
"""

import logging
import re
import xml.etree.ElementTree as ET
import zipfile
import zlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx

from goulash import config, geo

log = logging.getLogger("goulash.registry")

SOURCE_ID = "fns_rsmp"
SOURCE_TITLE = "Реестр МСП ФНС"
SOURCE_URL = "https://www.nalog.gov.ru/opendata/7707329152-rsmp/"

# Main OKVED class prefix -> (rubric title, taxonomy categories). Longest prefix wins. Animal feed (10.9) and
# tobacco (46.35) are not food suppliers and are absent on purpose.
OKVED: dict[str, tuple[str, tuple[str, ...]]] = {
    "10.1": ("Производство мяса и мясной продукции", ("meat",)),
    "10.12": ("Производство мяса птицы", ("poultry",)),
    "10.2": ("Переработка рыбы и морепродуктов", ("fish",)),
    "10.3": ("Переработка овощей и фруктов", ("vegetables",)),
    "10.32": ("Производство соков", ("drinks",)),
    "10.39": ("Консервирование овощей и фруктов", ("vegetables", "canned")),
    "10.4": ("Производство масел и жиров", ("oils",)),
    "10.51": ("Производство молочной продукции и сыров", ("dairy", "cheese")),
    "10.52": ("Производство мороженого", ("icecream",)),
    "10.6": ("Мукомольное производство, крахмалы", ("grocery",)),
    "10.7": ("Производство хлеба и мучных изделий", ("bakery",)),
    "10.73": ("Производство макаронных изделий", ("grocery",)),
    "10.81": ("Производство сахара", ("grocery",)),
    "10.82": ("Производство какао, шоколада и кондитерских изделий", ("chocolate", "confectionery")),
    "10.83": ("Производство чая и кофе", ("coffee_tea",)),
    "10.84": ("Производство приправ и пряностей", ("spices", "sauces")),
    "10.85": ("Производство готовых блюд", ("ready",)),
    "10.86": ("Производство детского и диетического питания", ("baby_health",)),
    "10.89": ("Производство прочих пищевых продуктов", ("ingredients",)),
    "11.01": ("Производство алкогольных напитков", ("alcohol",)),
    "11.02": ("Производство вина", ("alcohol",)),
    "11.03": ("Производство сидра", ("alcohol",)),
    "11.05": ("Производство пива", ("alcohol",)),
    "11.07": ("Производство безалкогольных напитков и воды", ("drinks",)),
    "46.31": ("Оптовая торговля фруктами и овощами", ("vegetables",)),
    "46.32": ("Оптовая торговля мясом и мясными продуктами", ("meat",)),
    "46.33": ("Оптовая торговля молочными продуктами, яйцами, маслами", ("dairy", "oils")),
    "46.34": ("Оптовая торговля напитками", ("drinks",)),
    "46.36": ("Оптовая торговля сахаром, шоколадом и кондитерскими изделиями", ("confectionery",)),
    "46.37": ("Оптовая торговля кофе, чаем, какао и пряностями", ("coffee_tea", "spices")),
    "46.38": ("Оптовая торговля прочими пищевыми продуктами", ("grocery",)),
    "46.38.1": ("Оптовая торговля рыбой и морепродуктами", ("fish",)),
    "46.39": ("Неспециализированная оптовая торговля продуктами", ("grocery",)),
}

# Subject codes whose names our region dictionary cannot resolve by text.
REGION_CODES = {
    "77": "Москва и Московская область",
    "50": "Москва и Московская область",
    "78": "Санкт-Петербург и Ленинградская область",
    "47": "Санкт-Петербург и Ленинградская область",
    "86": "Ханты-Мансийский АО",
    "89": "Ямало-Ненецкий АО",
    "14": "Республика Саха (Якутия)",
}


ARCHIVE_RE = re.compile(r"data-(\d{2})(\d{2})(\d{4})-structure-\d{8}\.zip")
# www.nalog.gov.ru drops long downloads and ignores Range; the file host serves the same archives with resume.
FILE_HOST = "https://file.nalog.ru/opendata/7707329152-rsmp/"


def latest_archive() -> str:
    """Name of the newest monthly archive listed on the open-data page, e.g. data-10102026-structure-12052026.zip."""
    html = httpx.get(SOURCE_URL, timeout=30, headers={"User-Agent": config.USER_AGENT}, follow_redirects=True).text
    names = set(m.group(0) for m in ARCHIVE_RE.finditer(html))
    if not names:
        raise RuntimeError("no registry archive found on the open-data page")
    return max(names, key=lambda n: ARCHIVE_RE.fullmatch(n).group(3, 2, 1))


def first_bad_offset(path: Path) -> int | None:
    """None if every member decompresses with a valid CRC; else the byte offset where the archive goes wrong.

    The file server occasionally streams wrong bytes without any HTTP error, so a finished download is checked
    member by member and repaired from the first broken one instead of being thrown away."""
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError):
        return path.stat().st_size if path.exists() else 0  # no central directory yet: incomplete, resume
    with zf:
        for info in sorted(zf.infolist(), key=lambda i: i.header_offset):
            try:
                with zf.open(info) as f:
                    while f.read(1 << 22):
                        pass
            except (zipfile.BadZipFile, zlib.error, EOFError, OSError):
                return info.header_offset
    return None


def _fetch_tail(name: str, dest: Path, attempts: int) -> None:
    """Append the rest of the file, resuming after dropped connections."""
    for _ in range(attempts):
        have = dest.stat().st_size if dest.exists() else 0
        headers = {"User-Agent": config.USER_AGENT, "Range": f"bytes={have}-"}
        try:
            with httpx.stream("GET", FILE_HOST + name, headers=headers, timeout=60) as r:
                if r.status_code == 416:  # nothing left to fetch
                    return
                r.raise_for_status()
                with dest.open("ab" if r.status_code == 206 else "wb") as f:
                    for chunk in r.iter_bytes(1 << 20):
                        f.write(chunk)
            return
        except httpx.HTTPError as e:
            log.info("registry download interrupted at %s bytes: %s", dest.stat().st_size, e)
    raise RuntimeError(f"registry archive {name}: download keeps failing")


def download(name: str, dest: Path, attempts: int = 50, repairs: int = 5) -> Path:
    """Resumable, self-repairing download of a ~2 GB archive. A complete, valid file is not fetched again."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    marker = dest.with_suffix(".name")
    if marker.exists() and marker.read_text() != name:
        dest.unlink(missing_ok=True)
    marker.write_text(name)
    for _ in range(repairs + 1):
        _fetch_tail(name, dest, attempts)
        bad = first_bad_offset(dest)
        if bad is None:
            return dest
        log.warning("registry archive corrupt from byte %s, re-fetching from there", bad)
        with dest.open("r+b") as f:
            f.truncate(bad)
    raise RuntimeError(f"registry archive {name} is still corrupt after {repairs} repairs")


def okved_class(code: str | None) -> tuple[str, tuple[str, ...]] | None:
    if not code:
        return None
    best = max((p for p in OKVED if code == p or code.startswith(p + ".")), key=len, default=None)
    return OKVED[best] if best else None


@dataclass
class RegistryCompany:
    inn: str
    ogrn: str | None
    name: str
    okved: str
    rubric: str
    categories: list[str]
    region: str | None
    region_raw: str  # "Свердловская область, Екатеринбург" as written in the registry
    city: str | None
    msp_category: str | None


def _place(el: ET.Element | None) -> str:
    if el is None:
        return ""
    name, kind = el.get("Наим", "").strip(), el.get("Тип", "").strip().lower()
    if not name:
        return ""
    name = name.title()
    return name if kind in ("", "город", "г", "г.") else f"{name} {kind}"


def parse_document(doc: ET.Element) -> RegistryCompany | None:
    org = doc.find("ОргВклМСП")
    okved_el = doc.find("СвОКВЭД/СвОКВЭДОсн")
    if org is None or okved_el is None:
        return None
    code = okved_el.get("КодОКВЭД")
    cls = okved_class(code)
    if not cls:
        return None
    mn = doc.find("СведМН")
    code_region = mn.get("КодРегион") if mn is not None else None
    region_text = _place(mn.find("Регион")) if mn is not None else ""
    city_el = None
    if mn is not None:
        city_el = mn.find("Город") if mn.find("Город") is not None else mn.find("НаселПункт")
    city = city_el.get("Наим", "").strip().title() if city_el is not None and city_el.get("Наим") else None
    region_el = mn.find("Регион") if mn is not None else None
    if not city and region_el is not None and region_el.get("Тип", "").lower().rstrip(".") in ("г", "город"):
        city = region_el.get("Наим", "").strip().title() or None  # Москва, Санкт-Петербург, Севастополь
    hits = geo.find_regions(region_text)
    region = (
        REGION_CODES.get(code_region or "")
        or geo.region_of_city(region_text)
        or (hits[0][0] if hits else None)
        or geo.region_of_city(city)
    )
    return RegistryCompany(
        inn=org.get("ИННЮЛ", ""),
        ogrn=org.get("ОГРН"),
        name=(org.get("НаимОргСокр") or org.get("НаимОрг") or "").strip(),
        okved=code or "",
        rubric=cls[0],
        categories=list(cls[1]),
        region=region,
        region_raw=", ".join(p for p in (region_text, city) if p),
        city=city,
        msp_category=doc.get("КатСубМСП"),
    )


def iter_companies(zip_path: Path) -> Iterator[RegistryCompany]:
    """Stream the multi-gigabyte archive file by file; memory stays flat."""
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            if not name.lower().endswith(".xml"):
                continue
            try:
                with zf.open(name) as f:
                    for _, el in ET.iterparse(f, events=("end",)):
                        if el.tag != "Документ":
                            continue
                        company = parse_document(el)
                        el.clear()
                        if company and company.inn and company.name:
                            yield company
            except ET.ParseError as e:  # one malformed file must not block the other ~7600
                log.warning("registry file %s skipped: %s", name, e)


def upsert_many(conn, companies: list[RegistryCompany]) -> None:
    """Batched upsert (pipelined executemany): ~50k rows over a remote connection in about a minute."""
    with conn.cursor() as cur:
        cur.executemany(
            """
        INSERT INTO registry (inn, ogrn, name, okved, rubric, categories, region, region_raw, city, msp_category)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (inn) DO UPDATE SET
            ogrn = excluded.ogrn, name = excluded.name, okved = excluded.okved, rubric = excluded.rubric,
            categories = excluded.categories, region = excluded.region, region_raw = excluded.region_raw,
            city = excluded.city, msp_category = excluded.msp_category, seen_at = now()
            """,
            [
                (c.inn, c.ogrn, c.name, c.okved, c.rubric, c.categories, c.region, c.region_raw, c.city, c.msp_category)
                for c in companies
            ],
        )
