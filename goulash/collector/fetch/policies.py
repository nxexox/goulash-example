"""Politeness and resilience middleware."""

import random
import re
import threading
import time
import urllib.robotparser
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from goulash import config
from goulash.collector.fetch.base import (
    Blocked,
    Fetcher,
    FetchError,
    FetchRequest,
    FetchResponse,
    Middleware,
)


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


class DomainThrottle(Middleware):
    """One in-flight request per host and a minimal delay between requests to the same host."""

    def __init__(self, inner: Fetcher, delay: float = config.REQUEST_DELAY):
        super().__init__(inner)
        self.delay = delay
        self._locks: dict[str, threading.Lock] = {}
        self._last: dict[str, float] = {}
        self._guard = threading.Lock()

    def fetch(self, request: FetchRequest) -> FetchResponse:
        host = host_of(request.url)
        with self._guard:
            lock = self._locks.setdefault(host, threading.Lock())
        with lock:
            wait = self._last.get(host, 0) + self.delay - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            try:
                return self.inner.fetch(request)
            finally:
                self._last[host] = time.monotonic()


class Retry(Middleware):
    """Exponential backoff with jitter; honours Retry-After on 429/503."""

    RETRY_STATUS = {429, 500, 502, 503, 504}

    def __init__(self, inner: Fetcher, attempts: int = 3, base: float = 1.5, max_wait: float = 60):
        super().__init__(inner)
        self.attempts, self.base, self.max_wait = attempts, base, max_wait

    def _retry_after(self, resp: FetchResponse) -> float | None:
        value = resp.headers.get("retry-after")
        if not value:
            return None
        if value.isdigit():
            return float(value)
        try:
            return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
        except (TypeError, ValueError):
            return None

    def fetch(self, request: FetchRequest) -> FetchResponse:
        last_error: Exception | None = None
        for attempt in range(self.attempts):
            try:
                resp = self.inner.fetch(request)
                if resp.status not in self.RETRY_STATUS or attempt == self.attempts - 1:
                    return resp
                wait = self._retry_after(resp)
            except Blocked:
                raise
            except FetchError as e:
                if not e.retryable or attempt == self.attempts - 1:
                    raise
                last_error, wait = e, None
            backoff = wait if wait is not None else self.base * 2**attempt + random.uniform(0, self.base)
            time.sleep(min(backoff, self.max_wait))
        raise last_error or FetchError("retry exhausted")


class Robots(Middleware):
    """Checks robots.txt per host (cached). Unreachable robots.txt -> allowed, as per RFC 9309."""

    def __init__(self, inner: Fetcher, user_agent: str = config.USER_AGENT):
        super().__init__(inner)
        self.ua = user_agent
        self._cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._lock = threading.Lock()

    def _parser(self, url: str) -> urllib.robotparser.RobotFileParser | None:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        with self._lock:
            if origin in self._cache:
                return self._cache[origin]
        parser: urllib.robotparser.RobotFileParser | None = None
        try:
            resp = self.inner.fetch(FetchRequest(origin + "/robots.txt", meta={"robots_probe": True}))
            if resp.status < 400:
                parser = urllib.robotparser.RobotFileParser()
                parser.parse(resp.text.splitlines())
            elif resp.status in (401, 403):
                parser = urllib.robotparser.RobotFileParser()
                parser.disallow_all = True
        except FetchError:
            parser = None
        with self._lock:
            self._cache[origin] = parser
        return parser

    def fetch(self, request: FetchRequest) -> FetchResponse:
        if request.meta.get("respect_robots", True) and not request.meta.get("robots_probe"):
            parser = self._parser(request.url)
            if parser is not None and not parser.can_fetch(self.ua, request.url):
                raise Blocked(f"robots.txt disallows {request.url}")
        return self.inner.fetch(request)


class Budget(Middleware):
    """Hard cap on requests per run: protects sites and our costs from runaway crawls."""

    def __init__(self, inner: Fetcher, max_requests: int):
        super().__init__(inner)
        self.remaining = max_requests
        self._lock = threading.Lock()

    def fetch(self, request: FetchRequest) -> FetchResponse:
        with self._lock:
            if self.remaining <= 0:  # not the site's fault: retry in a later run
                raise FetchError("request budget exhausted", retryable=True)
            self.remaining -= 1
        return self.inner.fetch(request)


class BlockDetector(Middleware):
    """Turns captcha / anti-bot walls into Blocked so jobs go to manual review instead of retry loops."""

    # Challenge pages, not ordinary pages that merely embed a reCAPTCHA form widget.
    MARKERS = (b"cf-chl-", b"challenge-platform", b"ddos-guard", b"are you a robot", b"__qrator")
    TITLE_MARKERS = (
        b"captcha",
        b"just a moment",
        b"access denied",
        b"\xd0\xb4\xd0\xbe\xd1\x81\xd1\x82\xd1\x83\xd0\xbf "
        b"\xd0\xbe\xd0\xb3\xd1\x80\xd0\xb0\xd0\xbd\xd0\xb8\xd1\x87\xd0\xb5\xd0\xbd",
    )  # "доступ ограничен"
    TITLE_RE = re.compile(rb"<title[^>]*>(.*?)</title>", re.S | re.I)

    def fetch(self, request: FetchRequest) -> FetchResponse:
        resp = self.inner.fetch(request)
        if resp.status == 403:
            raise Blocked(f"anti-bot wall at {request.url} (HTTP 403)")
        if resp.status in (200, 429) and len(resp.body) < 50_000:
            head = resp.body[:50_000].lower()
            m = self.TITLE_RE.search(head)
            title = m.group(1) if m else b""
            if any(x in head for x in self.MARKERS) or any(x in title for x in self.TITLE_MARKERS):
                raise Blocked(f"anti-bot wall at {request.url} (HTTP {resp.status})")
        return resp
