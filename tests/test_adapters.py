"""Source adapters against snapshots saved during the PoC (PoC/poc)."""

import hashlib
from pathlib import Path

import pytest

from goulash.collector.adapters import ADAPTERS
from goulash.collector.html import parse_html

POC = Path(__file__).resolve().parent.parent / "PoC" / "poc"
PRODEXPO_CARD = (
    "https://icatalog.expocentr.ru/ru/exhibitions/870c9c18-e84b-11ef-80ce-a0d3c1fab97f/exhibitors/376864?stand=7C090"
)
PRODEXPO_LIST = (
    "https://icatalog.expocentr.ru/ru/exhibitions/870c9c18-e84b-11ef-80ce-a0d3c1fab97f/countries/Россия?page=93"
)
WF_CARD = "https://exhibitors-itegroup.exhibitoronlinemanual.com/worldfood-moscow-2026/ru/Exhibitor/ExbDetails/MzE4NzY="


def snapshot(url: str):
    path = POC / (hashlib.sha256(url.encode()).hexdigest()[:16] + ".raw")
    if not path.exists():
        pytest.skip("PoC snapshots are not available")
    return parse_html(path.read_bytes().decode("utf-8", "replace"), url)


def test_prodexpo_card():
    rec = ADAPTERS["prodexpo"].parse_card(snapshot(PRODEXPO_CARD))
    assert rec.name == "ПЛАНЕТА ВКУСА, ООО"
    assert rec.external_id == "376864"
    assert rec.website == "http://www.plvk.ru"
    assert rec.city == "Санкт-Петербург"
    assert rec.phones == ["+7 (812) 441-3723"]
    assert rec.emails == ["marketing@plvk.ru"]
    assert "Продукция халяль" in rec.rubrics
    assert rec.description.startswith("ООО «Планета вкуса»")
    # neighbouring exhibitors listed on the same page must not leak into the record
    assert not any("ZIBO" in r for r in rec.rubrics)


def test_prodexpo_listing_uses_real_pagination():
    page = snapshot(PRODEXPO_LIST)
    batch = next(ADAPTERS["prodexpo"].listing(lambda u: page, {"next_page": 93}, max_pages=1))
    assert len(batch.card_urls) == 10
    assert all("?stand=" not in u for u in batch.card_urls)
    assert batch.checkpoint == {"next_page": 94, "last_page": 144}


def test_worldfood_card():
    rec = ADAPTERS["worldfood"].parse_card(snapshot(WF_CARD))
    assert rec.name == "АВС Фрукты"
    assert rec.country == "Индия"
    assert rec.website == "https://www.abcfruits.com"
    assert rec.rubrics[:2] == ["Энергетические напитки", "Соки, нектары, смузи"]
    assert "Пюре из манго Тотапури" in rec.products


def test_worldfood_card_with_navigation_links():
    """Live pages have prev/next links between "back" and the title; the name must come from the title."""
    html = """<html><body><a href="/back">ВЕРНУТЬСЯ К СПИСКУ УЧАСТНИКОВ</a><a href="/n">СЛЕДУЮЩИЙ</a>
      <h1 class="font-bold company-title">ТЕСТ, ООО</h1><div>Россия</div>
      <span class="tag">Варенье, джемы</span><div>https://test.ru</div><div>О компании</div>
      <p>Описание компании.</p><div>Modal title</div></body></html>"""
    rec = ADAPTERS["worldfood"].parse_card(parse_html(html, WF_CARD))
    assert rec.name == "ТЕСТ, ООО"
    assert rec.country == "Россия"
    assert rec.website == "https://test.ru"
    assert rec.description == "Описание компании."
