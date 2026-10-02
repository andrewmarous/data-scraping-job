import hashlib
from pathlib import Path
import sqlite3


def connect(data_dir: Path) -> sqlite3.Connection:
    data_dir.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(data_dir / "collector.sqlite3", timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=FULL")
    return db


def migrate(db: sqlite3.Connection, directory: Path) -> None:
    exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='schema_migrations'").fetchone()
    known = dict(db.execute("SELECT version,checksum FROM schema_migrations")) if exists else {}
    paths = sorted(directory.glob("[0-9][0-9][0-9][0-9]_*.sql"))
    if not paths:
        raise ValueError("No migrations found")
    for path in paths:
        version = int(path.name[:4])
        body = path.read_bytes()
        checksum = hashlib.sha256(body).hexdigest()
        if version in known:
            if known[version] != checksum:
                raise ValueError("Applied migration checksum changed")
            continue
        try:
            db.executescript("BEGIN IMMEDIATE;\n" + body.decode() +
                             f"\nINSERT INTO schema_migrations VALUES({version},'{checksum}');\nCOMMIT;")
        except BaseException:
            db.rollback()
            raise


def write_measurements(db, records, retrieval_id):
    """Upsert a snapshot. The caller owns the transaction."""
    db.executemany(
        "INSERT INTO measurements VALUES(?,?,?,?) ON CONFLICT(station,measured_at) "
        "DO UPDATE SET temperature_c=excluded.temperature_c,retrieval_id=excluded.retrieval_id",
        [(*record, retrieval_id) for record in records],
    )
