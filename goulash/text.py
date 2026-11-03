"""Text normalisation shared by indexing and query parsing (they must stay identical)."""

import re
import unicodedata
from functools import lru_cache

import snowballstemmer

_ru = snowballstemmer.stemmer("russian")
_en = snowballstemmer.stemmer("english")
WORD = re.compile(r"[a-zа-я0-9]+", re.I)

STOPWORDS = {
    "и",
    "в",
    "во",
    "на",
    "для",
    "с",
    "со",
    "по",
    "от",
    "до",
    "из",
    "к",
    "или",
    "а",
    "the",
    "of",
    "and",
    "ооо",
    "ао",
    "оао",
    "зао",
    "пао",
    "ип",
    "тд",
    "гк",
    "llc",
    "ltd",
    "co",
    "поставщик",
    "поставщики",
    "поставщика",
    "поставщиков",
    "купить",
    "опт",
    "оптом",
    "оптовый",
    "оптовые",
    "производитель",
    "производители",
    "производителя",
    "дистрибьютор",
    "дистрибьюторы",
    "цена",
    "цены",
    "недорого",
    "найти",
    "нужен",
    "нужна",
    "нужно",
    "нужны",
    "доставка",
    "доставкой",
}


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").lower().replace("ё", "е")
    return re.sub(r"\s+", " ", s).strip()


@lru_cache(maxsize=50_000)
def stem(word: str) -> str:
    if re.fullmatch(r"[a-z]+", word):
        return _en.stemWord(word)
    if re.fullmatch(r"[а-я]+", word):
        return _ru.stemWord(word)
    return word


def words(s: str) -> list[str]:
    return WORD.findall(normalize(s))


def stems(s: str, drop_stop: bool = True) -> list[str]:
    return [stem(w) for w in words(s) if not (drop_stop and w in STOPWORDS) and len(w) > 1]


def stemmed_text(s: str) -> str:
    return " ".join(stems(s))
