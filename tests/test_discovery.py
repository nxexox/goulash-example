"""FNS registry parsing and website discovery: search -> candidate -> verification -> supplier. No network."""

import base64
import zipfile

import httpx

from goulash.collector import discovery, registry, worker
from goulash.collector.discovery import NotReady
from goulash.collector.fetch.base import FetchError, FetchResponse
from goulash.collector.html import parse_html

REGISTRY_XML = """<?xml version="1.0" encoding="utf-8"?>
<Файл ИдФайл="x" ВерсФорм="4.06" ТипИнф="РЕЕСТРМСП" КолДок="3">
 <Документ ИдДок="1" ДатаСост="10.10.2026" ДатаВклМСП="10.08.2016" ВидСубМСП="1" КатСубМСП="2" ПризНовМСП="2">
  <ОргВклМСП НаимОрг="ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ &quot;СЫРОВАРНЯ УРАЛ&quot;"
             НаимОргСокр="ООО &quot;СЫРОВАРНЯ УРАЛ&quot;" ИННЮЛ="6670000001" ОГРН="1106670000001"/>
  <СведМН КодРегион="66"><Регион Тип="ОБЛАСТЬ" Наим="СВЕРДЛОВСКАЯ"/><Город Тип="ГОРОД" Наим="ЕКАТЕРИНБУРГ"/></СведМН>
  <СвОКВЭД><СвОКВЭДОсн КодОКВЭД="10.51.4" НаимОКВЭД="Производство сыра" ВерсОКВЭД="2014"/></СвОКВЭД>
 </Документ>
 <Документ ИдДок="2" ДатаСост="10.10.2026" ВидСубМСП="1" КатСубМСП="1">
  <ОргВклМСП НаимОрг="ООО &quot;ФУРАЖ&quot;" ИННЮЛ="6670000002" ОГРН="1"/>
  <СведМН КодРегион="66"><Регион Тип="ОБЛАСТЬ" Наим="СВЕРДЛОВСКАЯ"/></СведМН>
  <СвОКВЭД><СвОКВЭДОсн КодОКВЭД="10.91" НаимОКВЭД="Корма" ВерсОКВЭД="2014"/></СвОКВЭД>
 </Документ>
 <Документ ИдДок="3" ДатаСост="10.10.2026" ВидСубМСП="2" КатСубМСП="1">
  <ИПВклМСП ИННФЛ="667000000003" ОГРНИП="1"><ФИОИП Фамилия="ИВАНОВ" Имя="ИВАН"/></ИПВклМСП>
  <СведМН КодРегион="77"><Регион Тип="ГОРОД" Наим="МОСКВА"/></СведМН>
  <СвОКВЭД><СвОКВЭДОсн КодОКВЭД="46.33" НаимОКВЭД="Молоко" ВерсОКВЭД="2014"/></СвОКВЭД>
 </Документ>
</Файл>"""

YANDEX_XML = """<?xml version="1.0" encoding="utf-8"?>
<yandexsearch version="1.0"><response><results><grouping>
 <group><doc><url>https://www.rusprofile.ru/id/1</url><title>СЫРОВАРНЯ УРАЛ</title></doc></group>
 <group><doc><url>https://syr-ural.ru/catalog</url><domain>syr-ural.ru</domain>
   <title>Сыроварня <hlword>Урал</hlword></title><passages><passage>Моцарелла оптом</passage></passages></doc></group>
 <group><doc><url>https://other-cheese.ru/</url><domain>other-cheese.ru</domain><title>Другой сыр</title></doc></group>
</grouping></results></response></yandexsearch>"""


def registry_zip(tmp_path):
    path = tmp_path / "rsmp.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("VO_RRMSPSV_0001.xml", REGISTRY_XML)
    return path


def test_registry_keeps_food_legal_entities_only(tmp_path):
    companies = list(registry.iter_companies(registry_zip(tmp_path)))
    assert [c.inn for c in companies] == ["6670000001"]  # feed producer and individual entrepreneur skipped
    c = companies[0]
    assert c.name == 'ООО "СЫРОВАРНЯ УРАЛ"'
    assert c.region == "Свердловская область" and c.city == "Екатеринбург"
    assert c.categories == ["dairy", "cheese"] and c.msp_category == "2"


def test_okved_longest_prefix():
    assert registry.okved_class("46.38.1")[1] == ("fish",)
    assert registry.okved_class("46.38.29")[1] == ("grocery",)
    assert registry.okved_class("10.91") is None
    assert registry.okved_class("46.35") is None


def test_yandex_xml_and_candidates_skip_aggregators():
    hits = discovery.parse_yandex_xml(YANDEX_XML)
    assert hits[1].title == "Сыроварня Урал" and hits[1].snippet == "Моцарелла оптом"
    assert discovery.candidate_urls(hits) == ["https://syr-ural.ru/", "https://other-cheese.ru/"]


def test_yandex_async_flow():
    raw = base64.b64encode(YANDEX_XML.encode()).decode()
    responses = iter([{"done": False}, {"done": True, "response": {"rawData": raw}}])

    def handler(request):
        if request.url.path.endswith("/searchAsync"):
            assert '"queryText"' in request.content.decode()
            return httpx.Response(200, json={"id": "op1", "done": False})
        return httpx.Response(200, json=next(responses))

    y = discovery.YandexSearch("k", "f", client=httpx.Client(transport=httpx.MockTransport(handler)))
    token = y.submit('"сыроварня урал" Екатеринбург')
    try:
        y.results(token)
        raise AssertionError("expected NotReady")
    except NotReady:
        pass
    assert len(y.results(token)) == 3


def test_verify_by_inn_or_full_name_and_city():
    page = parse_html("<title>Сыроварня Урал</title><p>ООО Сыроварня Урал, г. Екатеринбург</p>", "https://a.ru/")
    assert discovery.verify([page], "6670000001", 'ООО "СЫРОВАРНЯ УРАЛ"', "Екатеринбург") == "name+city"
    assert discovery.verify([page], "6670000001", 'ООО "СЫРОВАРНЯ УРАЛ"', "Пермь") is None
    req = parse_html("<p>Реквизиты: ИНН 6670000001 КПП 667001001</p>", "https://a.ru/rekvizity")
    assert discovery.verify([req], "6670000001", "ООО ДРУГОЕ", None) == "inn"
    assert discovery.verify([page], "6670000001", "ООО МОЛОКО", None) is None  # bare brand word is not enough
    # no city in the registry: the region must be on the site; with neither known only the INN counts
    assert discovery.verify([page], "1", 'ООО "СЫРОВАРНЯ УРАЛ"', None, "Свердловская область") is None
    reg = parse_html("<p>ООО Сыроварня Урал, Свердловская обл.</p>", "https://a.ru/")
    assert discovery.verify([reg], "1", 'ООО "СЫРОВАРНЯ УРАЛ"', None, "Свердловская область") == "name+region"
    assert discovery.verify([reg], "1", 'ООО "СЫРОВАРНЯ УРАЛ"', None, None) is None


class FakeSearch:
    def __init__(self, ready_after=1):
        self.calls = 0
        self.ready_after = ready_after

    def submit(self, query):
        return "op:1"

    def results(self, token):
        self.calls += 1
        if self.calls <= self.ready_after:
            raise NotReady(token)
        return discovery.parse_yandex_xml(YANDEX_XML)


class FakeFetcher:
    pages = {
        "https://syr-ural.ru/": '<title>Сыроварня Урал</title><a href="/contacts">Контакты</a>'
        "<p>Моцарелла и сулугуни. Доставка по Екатеринбургу.</p>",
        "https://syr-ural.ru/contacts": '<p>Телефон: <a href="tel:+73431112233">+7 343 111-22-33</a></p>'
        "<p>ООО «Сыроварня Урал», ИНН 6670000001</p>",
    }

    def fetch(self, request):
        if request.url not in self.pages:
            raise FetchError("not found", retryable=False)
        return FetchResponse(request.url, request.url, 200, {}, self.pages[request.url].encode())


def test_find_site_creates_supplier_after_deferred_search(conn, tmp_path):
    registry.upsert_many(conn, list(registry.iter_companies(registry_zip(tmp_path))))
    from goulash.collector import queue

    queue.enqueue(conn, "find_site", "registry:6670000001", {"inn": "6670000001"})
    conn.commit()
    search = FakeSearch()
    first = worker.run(kinds=["find_site"], max_jobs=5, fetcher=FakeFetcher(), search=search)
    assert first["deferred"] == 1
    conn.execute("UPDATE jobs SET run_after = now()")
    conn.commit()
    assert worker.run(kinds=["find_site"], max_jobs=5, fetcher=FakeFetcher(), search=search)["done"] == 1

    reg = conn.execute("SELECT * FROM registry WHERE inn = '6670000001'").fetchone()
    assert reg["discovery"] == "found" and reg["match"] == "inn" and reg["website"] == "https://syr-ural.ru/"
    s = conn.execute("SELECT * FROM suppliers WHERE id = %s", (reg["supplier_id"],)).fetchone()
    assert s["region"] == "Свердловская область" and "cheese" in s["categories"]
    phones = conn.execute("SELECT value FROM contacts WHERE supplier_id = %s", (s["id"],)).fetchall()
    assert phones and conn.execute("SELECT 1 FROM facts WHERE field = 'inn' AND method = 'registry'").fetchone()


def test_find_site_without_contacts_is_not_published(conn, tmp_path):
    registry.upsert_many(conn, list(registry.iter_companies(registry_zip(tmp_path))))
    from goulash.collector import queue

    queue.enqueue(conn, "find_site", "registry:6670000001", {"inn": "6670000001"})
    conn.commit()
    fetcher = FakeFetcher()
    fetcher.pages = {"https://syr-ural.ru/": "<p>ООО Сыроварня Урал, ИНН 6670000001</p>"}
    worker.run(kinds=["find_site"], max_jobs=5, fetcher=fetcher, search=FakeSearch(ready_after=0))
    assert conn.execute("SELECT discovery FROM registry").fetchone()["discovery"] == "no_contacts"
    assert conn.execute("SELECT count(*) AS n FROM suppliers").fetchone()["n"] == 0


def test_domain_guess_from_company_name(monkeypatch):
    domains = discovery.guess_domains(discovery.core_name('ООО "СЫРОВАРНЯ УРАЛ"'))
    assert domains[:2] == ["syrovarnyaural.ru", "syrovarnyaural.com"]
    assert "syrovarnya-ural.ru" in domains and "сыроварняурал.рф" in domains
    assert "ekoniva.ru" in discovery.guess_domains(discovery.core_name('АО "ЭКОНИВА-АПК ХОЛДИНГ"'))
    monkeypatch.setattr(discovery, "resolves", lambda d: d in {"syrovarnya-ural.ru", "сыроварняурал.рф"})
    g = discovery.DomainGuess()
    hits = g.results(g.submit(discovery.build_query('ООО "СЫРОВАРНЯ УРАЛ"', "Екатеринбург", None)))
    assert [h.url for h in hits] == ["https://syrovarnya-ural.ru/", "https://xn--80aae2bidndbd2a3guc.xn--p1ai/"]


def test_download_repairs_corrupted_bytes(tmp_path, monkeypatch):
    good = tmp_path / "good.zip"
    with zipfile.ZipFile(good, "w", zipfile.ZIP_DEFLATED) as z:
        for i in range(5):
            z.writestr(f"f{i}.xml", REGISTRY_XML * 20)
    data = good.read_bytes()
    dest = tmp_path / "rsmp.zip"
    broken = bytearray(data)
    middle = len(data) // 2
    broken[middle : middle + 200] = b"x" * 200  # the server streamed wrong bytes without an error
    dest.write_bytes(bytes(broken))
    (tmp_path / "rsmp.name").write_text("a.zip")

    def handler(request):
        start = int(request.headers["Range"].split("=")[1].rstrip("-"))
        if start >= len(data):
            return httpx.Response(416)
        return httpx.Response(206, content=data[start:])

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(registry.httpx, "stream", lambda method, url, **kw: client.stream(method, url, **kw))
    assert registry.first_bad_offset(dest) is not None
    registry.download("a.zip", dest)
    assert dest.read_bytes() == data and registry.first_bad_offset(dest) is None
