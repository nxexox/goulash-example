"""Dependency-free HTML reading: visible text lines, absolute links, JSON-LD blocks."""

import json
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin

SKIP = {"script", "style", "noscript", "template", "svg"}
BLOCK = {"p", "div", "li", "tr", "br", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "td", "dd", "dt"}


@dataclass
class Page:
    url: str
    title: str = ""
    lines: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    jsonld: list = field(default_factory=list)
    html: str = field(default="", repr=False)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


class _Parser(HTMLParser):
    def __init__(self, base: str):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.page = Page(url=base)
        self._skip = 0
        self._in_title = False
        self._ld: str | None = None
        self._buf: list[str] = []

    def _flush(self) -> None:
        line = " ".join("".join(self._buf).split())
        if line:
            self.page.lines.append(line)
        self._buf = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "script" and (a.get("type") or "").lower() == "application/ld+json":
            self._ld = ""
            return
        if tag in SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "a" and a.get("href"):
            href = a["href"].strip()
            if not href.startswith(("javascript:", "#")):
                self.page.links.append(href if href.startswith(("mailto:", "tel:")) else urljoin(self.base, href))
        if tag in BLOCK:
            self._flush()

    def handle_endtag(self, tag):
        if tag == "script" and self._ld is not None:
            try:
                self.page.jsonld.append(json.loads(self._ld))
            except ValueError:
                pass
            self._ld = None
            return
        if tag in SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag == "title":
            self._in_title = False
        if tag in BLOCK or tag == "a":
            self._flush()

    def handle_data(self, data):
        if self._ld is not None:
            self._ld += data
        elif self._in_title:
            self.page.title += data.strip()
        elif not self._skip:
            self._buf.append(data)


def parse_html(html: str, url: str) -> Page:
    p = _Parser(url)
    p.feed(html)
    p.close()
    p._flush()
    p.page.links = list(dict.fromkeys(p.page.links))
    p.page.html = html
    return p.page


def jsonld_nodes(data):
    if isinstance(data, dict):
        yield data
        for v in data.values():
            yield from jsonld_nodes(v)
    elif isinstance(data, list):
        for v in data:
            yield from jsonld_nodes(v)
