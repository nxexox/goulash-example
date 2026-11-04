from goulash import config
from goulash.collector.fetch.base import Blocked, Fetcher, FetchError, FetchRequest, FetchResponse
from goulash.collector.fetch.policies import BlockDetector, Budget, DomainThrottle, Retry, Robots
from goulash.collector.fetch.transport import HttpxTransport, NoProxy, RoundRobinProxies

__all__ = ["Blocked", "FetchError", "Fetcher", "FetchRequest", "FetchResponse", "build_fetcher"]


def build_fetcher(
    max_requests: int = 500,
    delay: float = config.REQUEST_DELAY,
    timeout: float = config.REQUEST_TIMEOUT,
    attempts: int = 3,
) -> Fetcher:
    """Default chain. Order matters: budget counts every attempt, robots is checked before throttling,
    retries happen inside the per-host throttle so backoff never bursts a host.

    Probing a guessed site uses a short timeout and a single attempt: a candidate that does not answer
    quickly is most likely not the company's site, and waiting on it stalls the whole discovery batch."""
    proxies = RoundRobinProxies(config.PROXIES) if config.PROXIES else NoProxy()
    transport = HttpxTransport(proxies=proxies, timeout=timeout)
    chain = Retry(BlockDetector(transport), attempts=attempts)
    return Robots(Budget(DomainThrottle(chain, delay=delay), max_requests))
