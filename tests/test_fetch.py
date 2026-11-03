"""Fetch middleware with a fake transport: no network."""

import pytest

from goulash.collector.fetch.base import Blocked, FetchError, FetchRequest, FetchResponse
from goulash.collector.fetch.policies import BlockDetector, Budget, Retry, Robots
from goulash.collector.fetch.transport import RoundRobinProxies


class Fake:
    def __init__(self, responses):
        self.responses = responses
        self.calls: list[str] = []

    def fetch(self, request):
        self.calls.append(request.url)
        r = self.responses[request.url]
        r = r.pop(0) if isinstance(r, list) else r
        if isinstance(r, Exception):
            raise r
        status, body = r
        return FetchResponse(request.url, request.url, status, {}, body.encode())


def test_retry_recovers_after_transient_errors(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    fake = Fake({"https://a.ru/": [FetchError("timeout"), (503, ""), (200, "ok")]})
    assert Retry(fake, attempts=3).fetch(FetchRequest("https://a.ru/")).status == 200
    assert len(fake.calls) == 3


def test_retry_does_not_retry_non_retryable(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    fake = Fake({"https://a.ru/": [FetchError("dns", retryable=False), (200, "ok")]})
    with pytest.raises(FetchError):
        Retry(fake).fetch(FetchRequest("https://a.ru/"))
    assert len(fake.calls) == 1


def test_robots_disallow_and_cache():
    fake = Fake({"https://a.ru/robots.txt": (200, "User-agent: *\nDisallow: /private/"), "https://a.ru/ok": (200, "x")})
    robots = Robots(fake)
    assert robots.fetch(FetchRequest("https://a.ru/ok")).status == 200
    with pytest.raises(Blocked):
        robots.fetch(FetchRequest("https://a.ru/private/1"))
    assert fake.calls.count("https://a.ru/robots.txt") == 1


def test_robots_missing_means_allowed():
    fake = Fake({"https://a.ru/robots.txt": (404, ""), "https://a.ru/x": (200, "")})
    assert Robots(fake).fetch(FetchRequest("https://a.ru/x")).status == 200


def test_budget_is_retryable_not_blocked():
    fake = Fake({"https://a.ru/": (200, "")})
    b = Budget(fake, max_requests=1)
    b.fetch(FetchRequest("https://a.ru/"))
    with pytest.raises(FetchError) as e:
        b.fetch(FetchRequest("https://a.ru/"))
    assert e.value.retryable and not isinstance(e.value, Blocked)


def test_block_detector_ignores_recaptcha_widget():
    page = '<html><title>Компания</title><script src="https://www.google.com/recaptcha/api.js"></script></html>'
    assert BlockDetector(Fake({"https://a.ru/": (200, page)})).fetch(FetchRequest("https://a.ru/")).status == 200
    wall = "<html><title>Just a moment...</title></html>"
    with pytest.raises(Blocked):
        BlockDetector(Fake({"https://a.ru/": (200, wall)})).fetch(FetchRequest("https://a.ru/"))


def test_proxy_pool_benches_failing_proxy():
    pool = RoundRobinProxies(["http://p1", "http://p2"], cooldown=60)
    req = FetchRequest("https://a.ru/")
    pool.report("http://p1", ok=False)
    assert {pool.pick(req) for _ in range(4)} == {"http://p2"}
