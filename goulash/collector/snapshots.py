"""Immutable content-addressed snapshots: facts reference the exact bytes they were parsed from."""

import gzip
import hashlib
from pathlib import Path

from goulash import config


class SnapshotStore:
    """Local directory store. An S3-compatible store can implement the same two methods."""

    def __init__(self, root: Path = config.SNAPSHOT_DIR):
        self.root = root

    def _path(self, digest: str) -> Path:
        return self.root / digest[:2] / f"{digest}.html.gz"

    def put(self, body: bytes) -> str:
        digest = hashlib.sha256(body).hexdigest()
        path = self._path(digest)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(gzip.compress(body))
        return digest

    def get(self, digest: str) -> bytes | None:
        path = self._path(digest)
        return gzip.decompress(path.read_bytes()) if path.exists() else None
