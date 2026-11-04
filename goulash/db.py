from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from goulash import config

SCHEMA = Path(__file__).with_name("schema.sql")
_pool: ConnectionPool | None = None


def connect(url: str | None = None) -> psycopg.Connection:
    # TCP keepalives: managed Postgres (Neon) and NATs drop silent connections during long crawl steps.
    return psycopg.connect(
        url or config.DATABASE_URL, row_factory=dict_row, keepalives=1, keepalives_idle=30, keepalives_interval=10
    )


def init_schema(conn: psycopg.Connection) -> None:
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(4242)")  # concurrent starts (web + worker)
        conn.execute(SCHEMA.read_text())


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(config.DATABASE_URL, min_size=1, max_size=5, kwargs={"row_factory": dict_row}, open=True)
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def conn_from_pool():
    with pool().connection() as conn:
        yield conn
