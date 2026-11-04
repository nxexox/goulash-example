"""Find a registry company's own website and verify it is really that company.

Candidate providers are pluggable (`SearchProvider`). Without credentials the default is `DomainGuess`:
transliterate the company name into likely domains and keep those that resolve in DNS. No search engine is
scraped. With a key, Yandex Search API v2 is used in deferred mode
(the cheapest tier: results arrive in 5 minutes to a few hours and are kept 12 hours; the job is
re-scheduled until then). Without credentials
discovery is disabled; nothing else depends on it.
"""

import base64
import re
import socket
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Protocol

import httpx

from goulash import config
from goulash.collector.extract import INN_RE, SiteExtraction
from goulash.collector.html import Page
from goulash.text import normalize

# Company directories, marketplaces and job/maps sites: they mention the company but are not its site.
AGGREGATORS = re.compile(
    r"(^|\.)(rusprofile|list-org|checko|zachestnyibiznes|sbis|kontur|focus\.kontur|audit-it|spark-interfax|"
    r"e-ecolog|vbankcenter|tbank|tinkoff|sberbank|banki|ofdata|datanewton|egrul|nalog|rbc|kartoteka|"
    r"companies\.rbc|orgpage|spravker|zoon|yell|flamp|2gis|avito|ozon|wildberries|market\.yandex|hh|"
    r"superjob|rabota|zarplata|pulscen|tiu|satom|agroserver|foodmarkets|prodindustry|b2b-center|"
    r"tenderguru|zakupki|rostender|wikipedia|dzen|vc|habr|youtube|vk|ok|t|instagram|facebook|"
    r"exportcenter|moscow-export|fabricators|productcenter|trade\.su|bizly|bezformata|uslugio|"
    r"fek|synapsenet|sravni|clearspending|gosuslugi|crpt|chestnyznak|fsa|egrul\.nalog)\.\w+$"
)
LEGAL_FORMS = re.compile(
    r"\b(ооо|ао|пао|зао|оао|нао|пк|спк|скпк|кфх|гк|тд|ип|общество с ограниченной ответственностью|"
    r"акционерное общество|производственный кооператив|сельскохозяйственный производственный кооператив)\b",
    re.I,
)


@dataclass
class SearchHit:
    url: str
    title: str
    snippet: str = ""


class NotReady(Exception):
    """Deferred search has not finished yet: re-schedule the job."""


class SearchProvider(Protocol):
    def submit(self, query: str) -> str:
        """Start a search; returns a token for `results`."""

    def results(self, token: str) -> list[SearchHit]:
        """Raises NotReady while a deferred search is still running."""


def parse_yandex_xml(raw: bytes | str) -> list[SearchHit]:
    root = ET.fromstring(raw)
    hits = []
    for doc in root.iter("doc"):
        url = doc.findtext("url") or ""
        title_el = doc.find("title")
        title = "".join(title_el.itertext()) if title_el is not None else ""
        passages = " ".join("".join(p.itertext()) for p in doc.iter("passage"))
        hits.append(SearchHit(url=url, title=title.strip(), snippet=passages.strip()))
    return hits


class YandexSearch:
    """Yandex Search API v2. mode='async' (deferred, cheapest) or 'sync'."""

    API = "https://searchapi.api.cloud.yandex.net/v2/web"
    OPERATIONS = "https://operation.api.cloud.yandex.net/operations"

    def __init__(self, api_key: str, folder_id: str, mode: str = "async", client: httpx.Client | None = None):
        self.folder_id = folder_id
        self.mode = mode
        self.client = client or httpx.Client(timeout=30, headers={"Authorization": f"Api-Key {api_key}"})
        self._lock = threading.Lock()
        self._next_at = 0.0

    def _throttle(self, per_second: float = 8) -> None:
        """Quota: 10 requests/s per folder for both submit and result calls; stay below it across threads."""
        with self._lock:
            wait = self._next_at - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._next_at = max(time.monotonic(), self._next_at) + 1 / per_second

    def _body(self, query: str) -> dict:
        return {
            "query": {"searchType": "SEARCH_TYPE_RU", "queryText": query, "familyMode": "FAMILY_MODE_MODERATE"},
            "groupSpec": {"groupMode": "GROUP_MODE_DEEP", "groupsOnPage": "10", "docsInGroup": "1"},
            "maxPassages": "2",
            "region": "225",  # Russia
            "l10n": "LOCALIZATION_RU",
            "folderId": self.folder_id,
            "responseFormat": "FORMAT_XML",
        }

    def submit(self, query: str) -> str:
        self._throttle()
        if self.mode == "sync":
            r = self.client.post(f"{self.API}/search", json=self._body(query))
            r.raise_for_status()
            return "raw:" + r.json()["rawData"]
        r = self.client.post(f"{self.API}/searchAsync", json=self._body(query))
        r.raise_for_status()
        return "op:" + r.json()["id"]

    def results(self, token: str) -> list[SearchHit]:
        if token.startswith("raw:"):
            return parse_yandex_xml(base64.b64decode(token[4:]))
        self._throttle()
        r = self.client.get(f"{self.OPERATIONS}/{token.removeprefix('op:')}")
        r.raise_for_status()
        op = r.json()
        if not op.get("done"):
            raise NotReady(token)
        if "error" in op:
            raise RuntimeError(f"search failed: {op['error']}")
        return parse_yandex_xml(base64.b64decode(op["response"]["rawData"]))


TRANSLIT = {
    "а": ("a",), "б": ("b",), "в": ("v",), "г": ("g",), "д": ("d",), "е": ("e",), "ж": ("zh",), "з": ("z",),
    "и": ("i",), "й": ("y", "i"), "к": ("k",), "л": ("l",), "м": ("m",), "н": ("n",), "о": ("o",), "п": ("p",),
    "р": ("r",), "с": ("s",), "т": ("t",), "у": ("u",), "ф": ("f",), "х": ("h", "kh"), "ц": ("ts", "c"),
    "ч": ("ch",), "ш": ("sh",), "щ": ("sch", "shch"), "ъ": ("",), "ы": ("y",), "ь": ("",), "э": ("e",),
    "ю": ("yu", "ju"), "я": ("ya", "ja"),
}  # fmt: skip
# Words that rarely make it into a domain name.
GENERIC = {"торговый", "дом", "компания", "группа", "фирма", "производственная", "предприятие", "холдинг"}


def translit(word: str) -> list[str]:
    """Common spellings of a Cyrillic word in Latin: 'щука' -> ['schuka', 'shchuka']. Capped at 4 variants."""
    out = [""]
    for ch in word:
        opts = TRANSLIT.get(ch, (ch,) if ch.isascii() and ch.isalnum() else ("",))
        out = [o + v for o in out for v in opts][:4]
    return [o for o in dict.fromkeys(out) if o]


def guess_domains(core: str, limit: int = 20) -> list[str]:
    words_ = [w for w in re.findall(r"[a-zа-я0-9]+", core) if w]
    if not words_:
        return []
    variants = [words_]
    short = [w for w in words_ if w not in GENERIC]
    if short and short != words_:
        variants.append(short)
    if len(short) > 1 and len(short[0]) >= 5:
        variants.append(short[:1])  # brand word: "эконива-апк холдинг" -> ekoniva.ru
    names: list[str] = []
    for ws in variants:
        if len(ws) > 3:
            continue  # long legal names never become a domain verbatim
        latin = [translit(w) for w in ws]
        first = ["".join(p[0] for p in latin), "-".join(p[0] for p in latin)]
        alt = ["".join(p[-1] for p in latin), "-".join(p[-1] for p in latin)]
        names += first + alt
        if all(re.fullmatch(r"[а-я0-9]+", w) for w in ws):
            names.append("".join(ws) + ".рф")
    if 3 <= len(short) <= 5:
        # "малоярославецкий сыродельный завод" -> msz: plants and holdings often use the initials
        names.append("".join(translit(w)[0][0] for w in short if translit(w)))
    domains = []
    for n in dict.fromkeys(names):
        if n.endswith(".рф"):
            domains.append(n)
        elif 3 <= len(n) <= 40:
            domains += [f"{n}.ru", f"{n}.com"]
    return list(dict.fromkeys(domains))[:limit]


def resolves(domain: str) -> bool:
    try:
        socket.getaddrinfo(domain.encode("idna").decode(), 443)
        return True
    except (OSError, UnicodeError):
        return False


class DomainGuess:
    """No external search: likely domains from the company name, kept only if they exist in DNS.

    `submit` takes the query from `build_query` ('"core name" city') and uses the quoted name."""

    def submit(self, query: str) -> str:
        m = re.search(r'"([^"]+)"', query)
        return "guess:" + (m.group(1) if m else query)

    def results(self, token: str) -> list[SearchHit]:
        domains = guess_domains(token.removeprefix("guess:"))
        with ThreadPoolExecutor(max_workers=8) as pool:  # DNS lookups wait on the network, not the CPU
            alive = list(pool.map(resolves, domains))
        return [
            SearchHit(url=f"https://{d.encode('idna').decode() if not d.isascii() else d}/", title=d)
            for d, ok in zip(domains, alive, strict=True)
            if ok
        ]


def default_provider() -> SearchProvider:
    if config.YANDEX_SEARCH_API_KEY and config.YANDEX_FOLDER_ID:
        return YandexSearch(config.YANDEX_SEARCH_API_KEY, config.YANDEX_FOLDER_ID, config.YANDEX_SEARCH_MODE)
    return DomainGuess()


def core_name(name: str) -> str:
    """'ООО "МОЛОЧНЫЙ ЗАВОД ВЕРХНЕВОЛЖСКИЙ"' -> 'молочный завод верхневолжский'."""
    s = LEGAL_FORMS.sub(" ", normalize(name))
    s = re.sub(r"[\"«»'“”„()]", " ", s)
    return re.sub(r"\s+", " ", s).strip(" .,-")


def build_query(name: str, city: str | None, region: str | None) -> str:
    place = city or (region or "").split(" и ")[0]
    return f'"{core_name(name)}" {place}'.strip()


def candidate_urls(hits: list[SearchHit], limit: int = 3) -> list[str]:
    """Homepages of distinct non-aggregator domains, in search order."""
    from goulash.collector.ingest import domain_of

    out: dict[str, str] = {}
    for h in hits:
        domain = domain_of(h.url)
        if not domain or AGGREGATORS.search(domain) or domain in out:
            continue
        out[domain] = f"https://{domain}/"
        if len(out) >= limit:
            break
    return list(out.values())


def verify(pages: list[Page], inn: str, name: str, city: str | None, region: str | None = None) -> str | None:
    """How the site was matched to the registry company: 'inn' (strong), 'name+city' (medium) or None.

    INN printed anywhere on the site is decisive. Without it the full company name and the registered place must
    both appear: a bare brand word ("молоко") is too common to identify a company. The place is the city, or the
    region when the registry has no city; with neither known only the INN counts.
    """
    raw = " ".join(" ".join(p.lines) for p in pages)
    text = normalize(" ".join([*(p.title or "" for p in pages), raw]))
    if any(m.group(1) == inn for m in INN_RE.finditer(raw)) or (inn in text and "инн" in text):
        return "inn"
    core = core_name(name)
    if len(core) < 6 or core not in text:
        return None
    if city:
        return "name+city" if normalize(city) in text else None
    if region:
        # "Свердловская область" -> "свердловск": matches any case form of the region name on the page
        key = next((w for w in normalize(region).split() if w not in {"и", "область", "край", "республика"}), "")
        return "name+region" if len(key) >= 5 and key[:-2] in text else None
    return None


def has_contacts(pages: list[SiteExtraction]) -> bool:
    return any(p.phones or p.emails for p in pages)
