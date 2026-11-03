"""Fetch layer contracts.

A Fetcher is anything with `fetch(request) -> response`. Scraping features are composed as middleware
around a transport, so new tricks (proxy pools, header rotation, headless browser, captcha routing,
conditional GET cache) plug in without touching source adapters:

    fetcher = build_fetcher()  # Budget -> Robots -> Retry -> Throttle -> HttpxTransport(proxies, headers)
"""

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class FetchRequest:
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    # Free-form hints for middleware: {"render_js": True}, {"proxy_group": "ru"}, {"respect_robots": False}
    meta: dict = field(default_factory=dict)


@dataclass
class FetchResponse:
    url: str
    final_url: str
    status: int
    headers: dict[str, str]
    body: bytes
    elapsed: float = 0.0
    proxy: str | None = None

    @property
    def text(self) -> str:
        charset = "utf-8"
        ctype = self.headers.get("content-type", "")
        if "charset=" in ctype:
            charset = ctype.split("charset=")[-1].split(";")[0].strip() or charset
        try:
            return self.body.decode(charset, errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


class FetchError(Exception):
    """Network-level failure (DNS, TLS, timeout). `retryable` drives the job queue state."""

    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


class Blocked(FetchError):
    """Refused by policy (robots.txt, budget) or by the site (captcha, 403 wall). Not retried blindly."""

    def __init__(self, message: str):
        super().__init__(message, retryable=False)


class Fetcher(Protocol):
    def fetch(self, request: FetchRequest) -> FetchResponse: ...


class Middleware:
    """Base for wrapping fetchers. Override `fetch` and call `self.inner.fetch(...)`."""

    def __init__(self, inner: Fetcher):
        self.inner = inner

    def fetch(self, request: FetchRequest) -> FetchResponse:
        return self.inner.fetch(request)
