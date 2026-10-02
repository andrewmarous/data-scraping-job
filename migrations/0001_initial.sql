CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, checksum TEXT NOT NULL);
CREATE TABLE runs (
    id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL CHECK(status IN ('running','succeeded','failed','quarantined','interrupted')),
    parser_version TEXT NOT NULL,
    retrieval_id TEXT,
    record_count INTEGER,
    error_class TEXT,
    replay INTEGER NOT NULL
);
CREATE TABLE retrievals (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id),
    retrieved_at TEXT NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    byte_count INTEGER NOT NULL,
    content_type TEXT NOT NULL
);
CREATE TABLE measurements (
    station TEXT NOT NULL,
    measured_at TEXT NOT NULL,
    temperature_c REAL NOT NULL,
    retrieval_id TEXT NOT NULL REFERENCES retrievals(id),
    PRIMARY KEY(station, measured_at)
);
CREATE TABLE checkpoint (
    id INTEGER PRIMARY KEY CHECK(id=1),
    measured_at TEXT NOT NULL
);
