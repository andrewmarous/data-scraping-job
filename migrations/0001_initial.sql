CREATE TABLE schema_migrations(
 version INTEGER PRIMARY KEY, applied_at_utc TEXT NOT NULL, checksum TEXT NOT NULL
);
CREATE TABLE collection_runs(
 run_id TEXT PRIMARY KEY, source TEXT NOT NULL, partition_key TEXT, command TEXT NOT NULL,
 collector_version TEXT NOT NULL, parser_version TEXT NOT NULL, started_at_utc TEXT NOT NULL,
 finished_at_utc TEXT, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
 retrieved_count INTEGER NOT NULL DEFAULT 0, parsed_count INTEGER NOT NULL DEFAULT 0,
 inserted_count INTEGER NOT NULL DEFAULT 0, revised_count INTEGER NOT NULL DEFAULT 0,
 error_class TEXT, error_message TEXT,
 CHECK(status IN ('running','succeeded','failed','quarantined','skipped_already_running'))
);
CREATE TABLE raw_retrievals(
 retrieval_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES collection_runs(run_id), source TEXT NOT NULL,
 endpoint TEXT NOT NULL, request_parameters_json TEXT NOT NULL, retrieved_at_utc TEXT NOT NULL,
 source_as_of TEXT, http_status INTEGER, response_headers_json TEXT NOT NULL, body_path TEXT,
 body_sha256 TEXT, body_bytes INTEGER, collector_version TEXT NOT NULL, parser_version TEXT NOT NULL,
 attempt_number INTEGER NOT NULL, error_class TEXT, error_message TEXT
);
CREATE TABLE openrouter_daily_tokens(
 observation_id TEXT PRIMARY KEY, observation_date TEXT NOT NULL, model_permaslug TEXT NOT NULL,
 total_tokens_text TEXT NOT NULL, is_other INTEGER NOT NULL CHECK(is_other IN (0,1)),
 source_meta_json TEXT NOT NULL, retrieval_id TEXT NOT NULL REFERENCES raw_retrievals(retrieval_id),
 observed_at_utc TEXT NOT NULL, supersedes_observation_id TEXT REFERENCES openrouter_daily_tokens(observation_id),
 is_current INTEGER NOT NULL CHECK(is_current IN (0,1))
);
CREATE UNIQUE INDEX openrouter_current_row ON openrouter_daily_tokens(observation_date, model_permaslug) WHERE is_current=1;
CREATE INDEX runs_source_started ON collection_runs(source, started_at_utc DESC);
CREATE INDEX tokens_date ON openrouter_daily_tokens(observation_date, is_current);
CREATE VIEW openrouter_daily_total AS
 SELECT observation_date, SUM(CAST(total_tokens_text AS INTEGER)) AS token_volume
 FROM openrouter_daily_tokens WHERE is_current=1 GROUP BY observation_date;
