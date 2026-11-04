"""Pure-Python rules: normalisation, geo, taxonomy, query parsing, fact extraction."""

from goulash import geo
from goulash.collector.extract import extract_site, inn_valid, normalize_phone, text_facts
from goulash.collector.html import parse_html
from goulash.search.query import parse
from goulash.taxonomy import categories_for_text
from goulash.text import stem


def test_city_forms_and_aliases():
    assert [c for c, _, _ in geo.find_cities("сыр в Екатеринбурге")] == ["Екатеринбург"]
    assert [c for c, _, _ in geo.find_cities("доставка по Нижнему Новгороду")] == ["Нижний Новгород"]
    assert [c for c, _, _ in geo.find_cities("мука спб")] == ["Санкт-Петербург"]
    assert geo.region_of_city("Подольск") == "Москва и Московская область"


def test_city_from_address():
    assert geo.city_from_address("Россия, 193079, г. Санкт-Петербург, Октябрьская наб.") == "Санкт-Петербург"
    assert geo.city_from_address("385440, Республика Адыгея, аул Хакуринохабль") is None
    assert geo.region_from_address("385440, Республика Адыгея, аул Хакуринохабль") == "Республика Адыгея"


def test_taxonomy_maps_rubrics():
    assert categories_for_text("Пряности, специи, приправы, дрожжи") == ["spices"]
    assert "cheese" in categories_for_text("Молочная продукция, мороженое, сыры")
    assert "packaging" in categories_for_text("Контейнеры одноразовые")


def test_query_parsing_separates_product_region_quantity():
    q = parse("моцарела 2 кг Екатеринбург")
    assert [t.word for t in q.terms] == ["моцарела"]
    assert q.region == "Свердловская область"
    assert q.quantities == ["2 кг"]
    assert "cheese" in q.category_ids
    assert any("фасовка или объём" in h for h in q.hints)


def test_query_region_by_name():
    q = parse("соки краснодарский край")
    assert q.region == "Краснодарский край" and [t.word for t in q.terms] == ["соки"]


def test_query_drops_stopwords():
    q = parse("купить оптом шоколад поставщики")
    assert [t.stem for t in q.terms] == [stem("шоколад")]


def test_inn_checksum():
    assert inn_valid("7707083893")  # ПАО Сбербанк
    assert not inn_valid("7707083894")
    assert inn_valid("500100732259")


def test_phone_normalisation():
    assert normalize_phone("8 (495) 783-68-62") == "+7 (495) 783-68-62"
    assert normalize_phone("+7 812 4413723") == "+7 (812) 441-37-23"
    assert normalize_phone("12-34") is None


def test_text_facts_moq_certificates_delivery():
    lines = [
        "Минимальная сумма заказа 15 000 руб.",
        "Продукция сертифицирована по FSSC 22000, есть сертификат Халяль.",
        "Доставка по всей России транспортными компаниями, по Казани — бесплатно.",
        "ИНН 7707083893",
        "Сыр моцарелла от 520 ₽/кг",
    ]
    facts = {(f.field, f.value) for f in text_facts(lines, "https://example.ru")}
    assert ("moq", "15 000 руб") in facts
    assert ("certificates", "FSSC 22000") in facts
    assert ("certificates", "Халяль") in facts
    assert ("delivery", "по всей России") in facts
    assert ("delivery_regions", "Республика Татарстан") in facts
    assert ("inn", "7707083893") in facts
    assert ("price", "от 520 ₽/кг") in facts


def test_text_facts_ignores_bare_currency():
    facts = text_facts(["Корзина 0 ₽", "Цены от производителя"], "https://example.ru")
    assert not [f for f in facts if f.field in ("price", "moq")]


def test_extract_site_contacts_and_subpages():
    html = """<html><head><script type="application/ld+json">
      {"@type": "Organization", "telephone": "+7 (343) 111-22-33"}</script></head><body>
      <a href="tel:+74951234567">звоните</a> <a href="mailto:Sales@Example.ru">почта</a>
      <a href="/kontakty/">Контакты</a> <a href="https://other.ru/dostavka">чужой</a>
      <p>Доставка по Екатеринбургу и Челябинску.</p></body></html>"""
    ext = extract_site(parse_html(html, "https://example.ru/"))
    assert "+7 (495) 123-45-67" in ext.phones and "+7 (343) 111-22-33" in ext.phones
    assert ext.emails == ["sales@example.ru"]
    assert ext.subpages == ["https://example.ru/kontakty/"]
    regions = {f.value for f in ext.facts if f.field == "delivery_regions"}
    assert regions == {"Свердловская область", "Челябинская область"}


def test_free_delivery_threshold_is_not_moq():
    facts = {
        (f.field, f.value)
        for f in text_facts(["Минимальная сумма заказа - 2000 руб. Бесплатная доставка - от 5000 руб."], "u")
    }
    assert ("moq", "2000 руб") in facts
    assert ("delivery", "Бесплатная доставка от 5000 руб") in facts
    assert ("moq", "5000 руб") not in facts


def test_delivery_region_needs_delivery_context():
    def regions(line):
        return {f.value for f in text_facts([line], "u") if f.field == "delivery_regions"}

    assert regions("Доставка по Санкт-Петербургу выполняется бесплатно") == {"Санкт-Петербург и Ленинградская область"}
    assert regions("Оформление и оплата — до 16:00 по Москве в день доставки.") == set()
    assert regions("Отгрузки с собственного склада в Новороссийске, доставка транспортной компанией.") == set()


def test_price_without_product_and_third_party_inn_are_skipped():
    assert not text_facts(["100 ₽/кг Подробнее"], "u")
    assert not [f for f in text_facts(["Доставка силами Партнера ООО Детека. ИНН 7707083893"], "u") if f.field == "inn"]
    assert not [f for f in text_facts(["Organic Mango Farming"], "u") if f.field == "certificates"]


def test_query_place_moves_out_of_the_text():
    q = parse("купить моцарелла для пиццы 2 кг в питер")
    assert q.region == "Санкт-Петербург и Ленинградская область" and q.place == "питер"
    assert q.product_text == "моцарелла для пиццы 2 кг"
    assert parse("что-то питер").product_text == "" and not parse("что-то питер").terms
    assert parse("сыр").place is None


def test_brand_names_map_to_categories():
    assert parse("сникерс").terms[0].categories == {"chocolate"}
    assert "coffee_tea" in parse("нескафе").category_ids
