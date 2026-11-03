"""HTTP transport with pluggable proxy and header providers."""

import itertools
import threading
import time
from typing import Protocol

import httpx

from goulash import config
from goulash.collector.fetch.base import FetchError, FetchRequest, FetchResponse


class ProxyProvider(Protocol):
    def pick(self, request: FetchRequest) -> str | None: ...
    def report(self, proxy: str | None, ok: bool) -> None: ...


class NoProxy:
    def pick(self, request: FetchRequest) -> str | None:
        return None

    def report(self, proxy: str | None, ok: bool) -> None:
        pass


class RoundRobinProxies:
    """Static pool from GOULASH_PROXIES. Failing proxies are benched for `cooldown` seconds.

    Replace with a provider backed by a paid pool / geo-targeted proxies when needed.
    """

    def __init__(self, proxies: list[str], cooldown: float = 300):
        self._cycle = itertools.cycle(proxies)
        self._size = len(proxies)
        self._benched: dict[str, float] = {}
        self._cooldown = cooldown
        self._lock = threading.Lock()

    def pick(self, request: FetchRequest) -> str | None:
        with self._lock:
            for _ in range(self._size):
                p = next(self._cycle)
                if self._benched.get(p, 0) < time.monotonic():
                    return p
        return None  # all benched -> go direct rather than stall

    def report(self, proxy: str | None, ok: bool) -> None:
        if proxy and not ok:
            with self._lock:
                self._benched[proxy] = time.monotonic() + self._cooldown


class HeaderProvider(Protocol):
    def headers(self, request: FetchRequest) -> dict[str, str]: ...


class StaticHeaders:
    """Honest bot identification. A rotating provider can be swapped in here."""

    def headers(self, request: FetchRequest) -> dict[str, str]:
        return {
            "User-Agent": config.USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5",
        }


class HttpxTransport:
    def __init__(
        self,
        proxies: ProxyProvider | None = None,
        headers: HeaderProvider | None = None,
        timeout: float = config.REQUEST_TIMEOUT,
        max_bytes: int = config.MAX_BYTES,
    ):
        self.proxies = proxies or NoProxy()
        self.header_provider = headers or StaticHeaders()
        self.timeout = timeout
        self.max_bytes = max_bytes
        self._clients: dict[str | None, httpx.Client] = {}
        self._lock = threading.Lock()

    def _client(self, proxy: str | None) -> httpx.Client:
        with self._lock:
            if proxy not in self._clients:
                # TLS verification is never disabled.
                self._clients[proxy] = httpx.Client(
                    proxy=proxy, timeout=self.timeout, follow_redirects=True, max_redirects=5
                )
            return self._clients[proxy]

    def fetch(self, request: FetchRequest) -> FetchResponse:
        proxy = self.proxies.pick(request)
        headers = {**self.header_provider.headers(request), **request.headers}
        started = time.monotonic()
        try:
            with self._client(proxy).stream("GET", request.url, headers=headers) as r:
                body = bytearray()
                for chunk in r.iter_bytes():
                    body.extend(chunk)
                    if len(body) > self.max_bytes:
                        break
                resp = FetchResponse(
                    url=request.url,
                    final_url=str(r.url),
                    status=r.status_code,
                    headers={k.lower(): v for k, v in r.headers.items()},
                    body=bytes(body[: self.max_bytes]),
                    elapsed=time.monotonic() - started,
                    proxy=proxy,
                )
        except httpx.HTTPError as e:
            self.proxies.report(proxy, ok=False)
            raise FetchError(f"{type(e).__name__}: {e}") from e
        self.proxies.report(proxy, ok=resp.status < 500 and resp.status not in (403, 407, 429))
        return resp
