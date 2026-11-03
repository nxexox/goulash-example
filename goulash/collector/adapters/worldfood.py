"""WorldFood Moscow 2026 exhibitor list (exhibitoronlinemanual.com).

Only the first listing page is server-rendered; further pages load via an AJAX endpoint
(PaginationData/). A follow-up adapter version should call that endpoint instead of guessing IDs.
"""

import html
import re

from goulash.collector.adapters.base import CatalogRecord, ListingBatch, SourceAdapter
from goulash.collector.html import Page

LISTING = "https://exhibitors-itegroup.exhibitoronlinemanual.com/worldfood-moscow-2026/ru/Exhibitor"
CARD_RE = re.compile(r"/ExbDetails/([^/?#]+)$")
TABS = {"О компании", "Продукты", "Брошюры", "Видео", "Бренды", "Новинки"}
TITLE_RE = re.compile(r"<h1[^>]*company-title[^>]*>\s*(.*?)\s*</h1>", re.S)
TAG_RE = re.compile(r'<span class="tag">\s*(.*?)\s*</span>', re.S)
STOP = ("Modal title", "Изображение избранной брошюры", "Название брошюры", "© авторское право")


class WorldFoodAdapter(SourceAdapter):
    id = "worldfood"
    title = "WorldFood Moscow 2026, список участников"
    edition = "2026"
    entry_url = LISTING

    def listing(self, get_page, checkpoint, max_pages):
        page: Page = get_page(LISTING)
        cards = list(dict.fromkeys(x for x in page.links if CARD_RE.search(x)))
        yield ListingBatch(cards, {"next_page": 1, "last_page": 1}, done=True)  # AJAX pages: not yet

    def external_id(self, url: str) -> str:
        m = CARD_RE.search(url)
        return m.group(1) if m else url

    def parse_card(self, page: Page) -> CatalogRecord | None:
        lines = page.lines
        m = TITLE_RE.search(page.html)
        title = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", m.group(1))).split()) if m else ""
        try:
            # Start at the company title; prev/next navigation links may sit between "back" and the title.
            start = next(i for i, x in enumerate(lines) if title and x.upper() == title.upper())
        except StopIteration:
            return None
        body = []
        for x in lines[start:]:
            if x.startswith(STOP):
                break
            body.append(x)
        if len(body) < 2:
            return None
        name, country = title, body[1]
        rubrics = list(dict.fromkeys(html.unescape(x) for x in TAG_RE.findall(page.html)))
        website = next((x for x in body if re.match(r"https?://", x)), None)
        about = next((i for i, x in enumerate(body) if x == "О компании"), None)
        text = [x for x in body[about + 1 :] if x not in TABS] if about is not None else []
        return CatalogRecord(
            source=self.id,
            edition=self.edition,
            external_id=self.external_id(page.url),
            source_url=page.url,
            name=name,
            website=website,
            country=country,
            description=text[0] if text else "",
            rubrics=rubrics,
            products=[x for x in text[1:] if len(x) <= 120][:80],
        )
