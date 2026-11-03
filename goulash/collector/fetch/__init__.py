from goulash import config
from goulash.collector.fetch.base import Blocked, Fetcher, FetchError, FetchRequest, FetchResponse
from goulash.collector.fetch.policies import BlockDetector, Budget, DomainThrottle, Retry, Robots
from goulash.collector.fetch.transport import HttpxTransport, NoProxy, RoundRobinProxies

__all__ = ["Blocked", "FetchError", "Fetcher", "FetchRequest", "FetchResponse", "build_fetcher"]


def build_fetcher(max_requests: int = 500, delay: float = config.REQUEST_DELAY) -> Fetcher:
    """Default chain. Order matters: budget counts every attempt, robots is checked before throttling,
    retries happen inside the per-host throttle so backoff never bursts a host."""
    proxies = RoundRobinProxies(config.PROXIES) if config.PROXIES else NoProxy()
    transport = HttpxTransport(proxies=proxies)
    return Robots(Budget(DomainThrottle(Retry(BlockDetector(transport)), delay=delay), max_requests))
