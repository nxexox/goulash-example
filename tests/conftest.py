import os

import psycopg
import pytest

TEST_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://goulash:goulash@localhost:5439/goulash_test")


def _ensure_database(url: str) -> bool:
    info = psycopg.conninfo.conninfo_to_dict(url)
    name = info.pop("dbname")
    try:
        with psycopg.connect(psycopg.conninfo.make_conninfo(**info, dbname="postgres"), autocommit=True) as c:
            if not c.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone():
                c.execute(f'CREATE DATABASE "{name}"')
        return True
    except psycopg.OperationalError:
        return False


@pytest.fixture(scope="session")
def db_url():
    if not _ensure_database(TEST_URL):
        pytest.skip("PostgreSQL is not available (run `make db`)")
    from goulash import config

    config.DATABASE_URL = TEST_URL
    return TEST_URL


@pytest.fixture
def conn(db_url):
    from goulash import db

    c = db.connect(db_url)
    c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    c.commit()
    db.init_schema(c)
    c.commit()
    yield c
    c.close()
