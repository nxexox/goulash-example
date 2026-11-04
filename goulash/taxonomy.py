"""Canonical categories and synonym dictionary.

`aliases` are equivalent spellings / word forms (search expands to them).
`related` are neighbours (shown as suggestions, never silently merged): шоколад ≠ какао.
Version the dictionary: bump TAXONOMY_VERSION when matching semantics change.
"""

from dataclasses import dataclass, field

from goulash.text import stems

TAXONOMY_VERSION = "2026-11-04.1"


@dataclass(frozen=True)
class Category:
    id: str
    title: str
    aliases: tuple[str, ...]
    related: tuple[str, ...] = field(default=())


CATEGORIES: list[Category] = [
    Category(
        "dairy",
        "Молочная продукция",
        (
            "молоко",
            "молочный",
            "кефир",
            "творог",
            "сметана",
            "йогурт",
            "сливки",
            "ряженка",
            "масло сливочное",
            "простокваша",
            "молочная",
        ),
        ("cheese", "icecream"),
    ),
    Category(
        "cheese",
        "Сыры",
        (
            "сыр",
            "сыры",
            "моцарелла",
            "моцарела",
            "пармезан",
            "плавленый",
            "сулугуни",
            "брынза",
            "рикотта",
            "маскарпоне",
            "фета",
            "чеддер",
        ),
        ("dairy",),
    ),
    Category("icecream", "Мороженое", ("мороженое", "пломбир", "сорбет"), ("dairy", "frozen")),
    Category(
        "meat",
        "Мясо и мясная продукция",
        (
            "мясо",
            "мясной",
            "говядина",
            "свинина",
            "баранина",
            "колбаса",
            "колбасы",
            "сосиски",
            "ветчина",
            "деликатесы мясные",
            "фарш",
            "мясная",
        ),
        ("poultry",),
    ),
    Category(
        "poultry",
        "Птица и яйцо",
        ("курица", "куриный", "птица", "индейка", "утка", "бройлер", "яйцо", "яйца"),
        ("meat",),
    ),
    Category(
        "fish",
        "Рыба и морепродукты",
        ("рыба", "рыбный", "морепродукты", "лосось", "икра", "креветки", "кальмар", "форель", "сельдь", "рыбная"),
        ("frozen",),
    ),
    Category(
        "vegetables",
        "Овощи, фрукты, ягоды",
        (
            "овощи",
            "фрукты",
            "ягоды",
            "картофель",
            "зелень",
            "грибы",
            "плодоовощной",
            "плодовоовощная",
            "пюре фруктовое",
            "мякоть",
        ),
        ("frozen", "canned"),
    ),
    Category(
        "frozen",
        "Замороженные продукты и полуфабрикаты",
        (
            "замороженный",
            "заморозка",
            "полуфабрикаты",
            "пельмени",
            "вареники",
            "шоковая заморозка",
            "iqf",
            "быстрозамороженные",
        ),
        ("ready",),
    ),
    Category(
        "ready",
        "Готовая кулинария",
        ("готовая еда", "кулинария", "салаты", "сэндвичи", "готовые блюда", "суши"),
        ("frozen",),
    ),
    Category(
        "grocery",
        "Бакалея",
        (
            "бакалея",
            "крупа",
            "крупы",
            "мука",
            "макароны",
            "макаронные",
            "сахар",
            "соль",
            "рис",
            "гречка",
            "хлопья",
            "мюсли",
            "бобовые",
            "орехи",
            "сухофрукты",
            "семечки",
        ),
        ("spices", "bakery"),
    ),
    Category(
        "spices",
        "Специи и приправы",
        ("специи", "приправы", "пряности", "перец", "горчица", "хрен", "дрожжи"),
        ("sauces",),
    ),
    Category(
        "sauces",
        "Соусы, майонез, кетчуп",
        ("соус", "соусы", "майонез", "кетчуп", "маринады", "заправки"),
        ("oils", "spices"),
    ),
    Category(
        "oils",
        "Масла и жиры",
        (
            "масло растительное",
            "подсолнечное",
            "оливковое",
            "растительные масла",
            "маргарин",
            "спреды",
            "жиры",
            "масложировая",
        ),
        ("sauces",),
    ),
    Category(
        "confectionery",
        "Кондитерские изделия",
        (
            "кондитерские",
            "конфеты",
            "карамель",
            "мармелад",
            "зефир",
            "пастила",
            "печенье",
            "вафли",
            "пряники",
            "халва",
            "торты",
            "пирожные",
            "драже",
            "ирис",
            "леденцы",
            "грильяж",
        ),
        ("chocolate", "bakery"),
    ),
    Category(
        "chocolate",
        "Шоколад и какао",
        (
            "шоколад",
            "шоколадный",
            "какао",
            "батончики",
            "глазурь",
            "шоколадные",
            # brands people search by; a match is labelled "по категории", the brand itself is not confirmed
            "сникерс",
            "snickers",
            "марс",
            "твикс",
            "twix",
            "баунти",
            "bounty",
            "киткат",
            "kitkat",
            "милка",
            "milka",
            "альпен",
            "alpen",
            "киндер",
            "kinder",
            "риттер",
            "ritter",
            "nesquik",
            "несквик",
        ),  # fmt: skip
        ("confectionery",),
    ),
    Category(
        "bakery",
        "Хлеб и выпечка",
        ("хлеб", "хлебобулочные", "выпечка", "батон", "булочки", "хлебцы", "сухари", "сушки", "тесто", "лаваш"),
        ("grocery", "confectionery"),
    ),
    Category(
        "drinks",
        "Безалкогольные напитки",
        (
            "напитки",
            "сок",
            "соки",
            "нектары",
            "вода",
            "минеральная",
            "лимонад",
            "квас",
            "морс",
            "энергетик",
            "энергетические",
            "смузи",
            "сиропы",
            "сироп",
            "кола",
            "пепси",
            "pepsi",
            "фанта",
            "спрайт",
            "добрый",
            "rich",
        ),
        ("coffee_tea",),
    ),
    Category(
        "coffee_tea",
        "Кофе и чай",
        (
            "кофе",
            "чай",
            "кофейный",
            "зерно кофе",
            "капсулы",
            "цикорий",
            "нескафе",
            "nescafe",
            "якобс",
            "jacobs",
            "лавацца",
            "lavazza",
            "липтон",
            "lipton",
            "гринфилд",
            "greenfield",
            "ахмад",
            "ahmad",
        ),  # fmt: skip
        ("drinks",),
    ),
    Category(
        "alcohol",
        "Алкогольные напитки",
        ("вино", "пиво", "сидр", "алкогольные", "коньяк", "водка", "ликер"),
        ("drinks",),
    ),
    Category(
        "canned",
        "Консервы и консервация",
        ("консервы", "консервация", "консервированные", "варенье", "джем", "джемы", "тушенка", "соленья"),
        ("vegetables",),
    ),
    Category(
        "ingredients",
        "Пищевые ингредиенты",
        (
            "ингредиенты",
            "ароматизаторы",
            "ароматизатор",
            "красители",
            "добавки",
            "стабилизаторы",
            "загустители",
            "крахмал",
            "эмульгаторы",
            "ферменты",
            "концентраты",
            "пищевые добавки",
            "улучшители",
        ),
        ("spices",),
    ),
    Category(
        "baby_health",
        "Детское и здоровое питание",
        (
            "детское питание",
            "здоровое питание",
            "диетические",
            "диабетические",
            "безглютеновые",
            "спортивное питание",
            "бады",
            "функциональное",
            "веганские",
            "растительные альтернативы",
        ),
        (),
    ),
    Category("halal", "Халяль и кошер", ("халяль", "кошер", "кошерные"), ()),
    Category(
        "packaging",
        "Упаковка",
        (
            "упаковка",
            "упаковочный",
            "контейнер",
            "контейнеры",
            "пленка",
            "коробка",
            "коробки",
            "лоток",
            "лотки",
            "стакан",
            "стаканы",
            "пакеты",
            "крышки",
            "одноразовая посуда",
            "гофрокороб",
            "этикетка",
            "тара",
            "бутылки",
        ),
        (),
    ),
    Category(
        "horeca",
        "Снабжение HoReCa",
        ("horeca", "хорека", "рестораны", "кафе", "общепит", "для ресторанов", "кейтеринг"),
        (),
    ),
    Category(
        "snacks",
        "Снеки",
        ("снеки", "чипсы", "сухарики", "попкорн", "орешки", "крекеры", "лейс", "lays", "принглс", "pringles"),
        ("grocery", "confectionery"),
    ),
]

BY_ID = {c.id: c for c in CATEGORIES}

# single stem -> category ids; multi-word aliases -> tuple of stems
_STEM_INDEX: dict[str, set[str]] = {}
_STEM_WORD: dict[str, str] = {}  # stem -> a readable word for it, to show a typo fix in the search box
_PHRASES: list[tuple[tuple[str, ...], str]] = []
for _c in CATEGORIES:
    for _a in (_c.title, *_c.aliases):
        _s = tuple(stems(_a, drop_stop=False))
        if len(_s) == 1:
            _STEM_INDEX.setdefault(_s[0], set()).add(_c.id)
            _STEM_WORD.setdefault(_s[0], _a.lower())
        elif _s:
            _PHRASES.append((_s, _c.id))


def word_for_stem(s: str) -> str | None:
    return _STEM_WORD.get(s)


def categories_for_text(text: str) -> list[str]:
    """Canonical categories mentioned in a source text (rubrics, description)."""
    toks = stems(text, drop_stop=False)
    found: dict[str, None] = {}
    for t in toks:
        for cid in sorted(_STEM_INDEX.get(t, ())):
            found[cid] = None
    joined = " " + " ".join(toks) + " "
    for phrase, cid in _PHRASES:
        if " " + " ".join(phrase) + " " in joined:
            found[cid] = None
    return list(found)


def categories_for_stem(s: str) -> set[str]:
    return set(_STEM_INDEX.get(s, ()))


def alias_stems(category_id: str) -> set[str]:
    c = BY_ID[category_id]
    out: set[str] = set()
    for a in (c.title, *c.aliases):
        out.update(stems(a, drop_stop=False))
    return out


def equivalent_stems(term_stem: str) -> set[str]:
    """Spelling variants of the same product (моцарела -> моцарелл). Never crosses into other products."""
    variants = {
        "моцарел": {"моцарелл"},
        "моцарелл": {"моцарел"},
        "кофейн": {"коф"},
        "хорек": {"horec"},
        "horec": {"хорек"},
    }
    return {term_stem, *variants.get(term_stem, ())}
