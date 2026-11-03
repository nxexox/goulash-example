"""Prodexpo (Expocentr) online catalog: Russian exhibitors, ~144 listing pages x 10 cards."""

import re
from urllib.parse import quote

from goulash.collector.adapters.base import CatalogRecord, ListingBatch, SourceAdapter, field_after
from goulash.collector.html import Page

EXHIBITION = "870c9c18-e84b-11ef-80ce-a0d3c1fab97f"
BASE = f"https://icatalog.expocentr.ru/ru/exhibitions/{EXHIBITION}"
LISTING = BASE + "/countries/" + quote("Россия") + "?page={page}"
CARD_RE = re.compile(r"/exhibitors/(\d+)")
PAGE_RE = re.compile(r"[?&]page=(\d+)")


class ProdexpoAdapter(SourceAdapter):
    id = "prodexpo"
    title = "Продэкспо-2026, интернет-каталог участников"
    edition = "2026"
    entry_url = LISTING.format(page=1)

    def listing(self, get_page, checkpoint, max_pages):
        page_no = int((checkpoint or {}).get("next_page", 1))
        last_page = None
        for _ in range(max_pages):
            page: Page = get_page(LISTING.format(page=page_no))
            cards = list(
                dict.fromkeys(
                    f"{BASE}/exhibitors/{m.group(1)}"  # drop ?stand=: one company, many stands
                    for link in page.links
                    if EXHIBITION in link and (m := CARD_RE.search(link))
                )
            )
            pages = [int(m.group(1)) for link in page.links if "/countries/" in link and (m := PAGE_RE.search(link))]
            last_page = max(pages + [page_no])
            done = page_no >= last_page or not cards
            next_page = 1 if done else page_no + 1  # full pass finished -> next run starts over
            yield ListingBatch(cards, {"next_page": next_page, "last_page": last_page}, done=done)
            if done:
                return
            page_no = next_page

    def external_id(self, url: str) -> str:
        m = CARD_RE.search(url)
        return m.group(1) if m else url

    def parse_card(self, page: Page) -> CatalogRecord | None:
        lines = page.lines
        try:
            start = next(i for i, x in enumerate(lines) if x.rstrip(":") == "Стенд")
        except StopIteration:
            return None
        end = next((i for i, x in enumerate(lines) if x.startswith("Предоставленная информация")), len(lines))
        body = lines[start:end]
        name = lines[start - 1] if start else page.title.split(":")[0]

        def section(label: str, stop: tuple[str, ...]) -> list[str]:
            try:
                i = body.index(label + ":")
            except ValueError:
                return []
            out = []
            for x in body[i + 1 :]:
                if x.rstrip(":") in stop:
                    break
                out.append(x)
            return out

        website = field_after(body, "Сайт")
        if website and not website.startswith("http"):
            website = "http://" + website
        phones = [p.strip() for p in re.split(r"[,;]", field_after(body, "Телефон") or "") if p.strip()]
        emails = re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", field_after(body, "E-mail") or "")
        return CatalogRecord(
            source=self.id,
            edition=self.edition,
            external_id=self.external_id(page.url),
            source_url=page.url,
            name=name,
            website=website,
            city=field_after(body, "Город"),
            country=field_after(body, "Страна"),
            address=field_after(body, "Адрес"),
            phones=phones,
            emails=emails,
            description="\n".join(section("Описание", ("Рубрики",))),
            rubrics=section("Рубрики", ()),
        )
