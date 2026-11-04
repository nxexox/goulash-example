"""Paths and runtime settings. Everything is overridable via environment variables."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("GOULASH_DATA_DIR", ROOT / "data"))
SEED_DIR = Path(os.environ.get("GOULASH_SEED_DIR", DATA_DIR / "seed"))
SNAPSHOT_DIR = Path(os.environ.get("GOULASH_SNAPSHOT_DIR", DATA_DIR / "snapshots"))
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://goulash:goulash@localhost:5439/goulash")

USER_AGENT = os.environ.get(
    "GOULASH_USER_AGENT", "GoulashSupplierBot/0.1 (+test assignment; low-rate catalog indexing)"
)
# Comma-separated proxy URLs, e.g. "http://user:pass@host:3128,socks5://host:1080". Empty = direct.
PROXIES = [p.strip() for p in os.environ.get("GOULASH_PROXIES", "").split(",") if p.strip()]
REQUEST_DELAY = float(os.environ.get("GOULASH_REQUEST_DELAY", "1.0"))
REQUEST_TIMEOUT = float(os.environ.get("GOULASH_REQUEST_TIMEOUT", "20"))
MAX_BYTES = int(os.environ.get("GOULASH_MAX_BYTES", str(5_000_000)))

# Search returns fewer results than this -> a discovery request is queued for the collector.
DISCOVERY_THRESHOLD = int(os.environ.get("GOULASH_DISCOVERY_THRESHOLD", "3"))

# Website discovery for registry companies (Yandex Search API v2). Empty key = discovery disabled.
YANDEX_SEARCH_API_KEY = os.environ.get("YANDEX_SEARCH_API_KEY", "")
YANDEX_FOLDER_ID = os.environ.get("YANDEX_FOLDER_ID", "")
YANDEX_SEARCH_MODE = os.environ.get("YANDEX_SEARCH_MODE", "async")  # async (deferred, cheap) | sync
REGISTRY_ZIP = Path(os.environ.get("GOULASH_REGISTRY_ZIP", DATA_DIR / "registry" / "rsmp.zip"))
