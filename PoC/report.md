# Зафиксированная архитектура

Онлайн: поиск по готовой базе, фильтры, ранжирование, сравнение.
Фон: обнаружение сайтов → очередь → HTTP → CPU-парсинг → нормализация → проверка → публикация в поисковом представлении. LLM — опционально для неоднозначных фрагментов.

# Проверка 25 сайтов

Дата: 4 ноября 2026 (Asia/Yekaterinburg). Проверены одиночные страницы, не весь сайт. Контакты — ссылки tel/mailto; их отсутствие не доказывает отсутствие контактов в тексте. HTTP 200 не подтверждает надежность поставщика.

|Компания|Категория|URL|Результат|Контактных ссылок|
|---|---|---|---|---|
|Наш Партнёр|HoReCa|https://our-partner.ru|<urlopen error _ssl.c:1064: The handshake operation timed out>|0|
|Мос-Милк|Молочная продукция|https://mos-milk.ru|<urlopen error _ssl.c:1064: The handshake operation timed out>|0|
|Ингредико|Ингредиенты|https://www.ingredico.ru|200|4|
|Фуд Фреш|HoReCa|https://food-fresh.ru|200|8|
|Horecafoods|HoReCa|https://horecafoods.ru|<urlopen error _ssl.c:1064: The handshake operation timed out>|0|
|Густорико|HoReCa|https://opentgp.ru/food/horeca/|200|5|
|AGAT FOODS|HoReCa|https://agatfoods.ru|200|0|
|АРОСА|HoReCa|https://arosa.ru|200|7|
|Неос Ингредиентс|Ингредиенты|https://neos-ingredients.ru|<urlopen error _ssl.c:1064: The handshake operation timed out>|0|
|GEOVITA|Упаковка|https://geo-vita.com|200|5|
|Ingred PRO|Барные ингредиенты|https://ingredpro.ru|402|0|
|Коррекс|Упаковка|https://www.korrex-td.ru|200|8|
|Tasty Coffee|Кофе|https://tastycoffee.ru|200|0|
|SFT Trading|Кофе|https://sfttrading.ru|<urlopen error [Errno 8] nodename nor servname provided, or not known>|0|
|Союзснаб|Ингредиенты|https://ssnab.ru|200|8|
|OSQ Group|Упаковка|https://osqgroup.ru/about/|200|8|
|Гиорд|Ингредиенты|https://www.giord.ru|<urlopen error _ssl.c:1064: The handshake operation timed out>|0|
|Мистерия|Упаковка|https://www.mystery.ru/catalogs/|200|8|
|Уральский завоз|Продукты|https://www.bazaopt.ru|200|2|
|Уралагротэкс|Продукты|https://uragrotex.ru|<urlopen error [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: Hostname mismatch, certificate is not valid for 'uragrotex.ru'. (_ssl.c:1082)>|0|
|Фуд Экспресс|Продукты|https://food-exress.ru/feedback/|200|4|
|ГрандПродуктЕкб|Продукты|https://grandproduktekb.ru|200|2|
|ТК ДИП|Продукты|https://tkdip.ru|200|8|
|Наш Союз|Молочная и мясная продукция|https://nash-souz.ru|200|4|
|Global Foods|HoReCa|https://globalfoods.ru|200|5|

# Пополнение

Источники: официальные каталоги выставок, отраслевые союзы, страницы дилеров производителей, поисковые запросы категория × регион × опт/производитель/дистрибьютор, предложения пользователей и поставщиков.

Каждый кандидат хранит discovered_from, discovered_at, domain, category_hint, region_hint, status. Стадии: discovered → fetched → classified → extracted → validated → published. Ошибки сети остаются retry_pending. Повторные ошибки переводятся в manual_review, но не означают закрытие компании.

Разделять discovery новых компаний и refresh известных. Приоритет: востребованность категории/региона, дефицит выдачи, вероятность нового домена, доступность источника, стоимость проверки. Проверять robots.txt и условия источника до массового обхода; эта проверка не выполнена в одиночном пробном запуске.

Метрики: новые уникальные поставщики на 100 кандидатов, стоимость опубликованной карточки, доля дубликатов, подтвержденные контакты, покрытие категория × регион, свежесть фактов.


## Навигация по материалам и PoC

- [Подробный план и схема архитектуры](PLAN.md)
- [Отчёт по 25 сайтам](report.md)
- [Проверка 25 сайтов — probe.py](probe.py)
- [Обнаружение источников — discover.py](discover.py)
- [Выборка карточек каталогов — sample_catalogs.py](sample_catalogs.py)
- [Загрузка страниц поставщиков — enrich.py](enrich.py)
- [Извлечение и обновление базы — pipeline.py](pipeline.py)
- [Парсинг данных поставщиков — extract_supplier.py](extract_supplier.py)
- [Поисковый PoC — search_poc.py](search_poc.py)
- [Результаты проверки сайтов](results.json)
- [Извлечённые кандидаты](poc/candidates.json)
- [Проверка обновлений](poc/pipeline_report.json)
- [Результаты поиска](poc/search_report.json)
- [Данные поставщиков](poc/supplier_facts.json)
