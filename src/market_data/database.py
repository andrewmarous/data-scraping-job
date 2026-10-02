from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .errors import MarketDataError


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


def initialize_dirs(data_dir: Path) -> None:
    for name in ("raw", "logs", "quarantine", "locks"):
        (data_dir / name).mkdir(parents=True, exist_ok=True)


def migrate(database_path: Path, migrations_dir: Path) -> list[int]:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = connect(database_path)
    applied: list[int] = []
    try:
        exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone()
        known = {} if not exists else dict(connection.execute("SELECT version, checksum FROM schema_migrations"))
        for path in sorted(migrations_dir.glob("[0-9][0-9][0-9][0-9]_*.sql")):
            version = int(path.name[:4])
            body = path.read_bytes()
            checksum = hashlib.sha256(body).hexdigest()
            if version in known:
                if known[version] != checksum:
                    raise MarketDataError(f"Applied migration checksum changed: {path.name}")
                continue
            # executescript commits an existing transaction, so the migration and
            # its ledger row must be part of the same script to remain atomic.
            applied_at = utc_now().replace("'", "''")
            script = body.decode()
            ledger = (
                "INSERT INTO schema_migrations(version,applied_at_utc,checksum) "
                f"VALUES({version},'{applied_at}','{checksum}');"
            )
            connection.executescript("BEGIN EXCLUSIVE;\n" + script + "\n" + ledger + "\nCOMMIT;")
            applied.append(version)
    finally:
        connection.close()
    return applied
