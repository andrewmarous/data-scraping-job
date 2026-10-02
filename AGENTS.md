# Adapt this collector

## Goal

Build one robust launchd job backed by local SQLite.
Modify this concrete example for the requested dataset. Do not add a registry, plugin system, or universal schema abstraction.

## Procedure

1. Read `README.md` and the source files.
2. Identify the source's access permissions, authentication, pagination, quotas, and data sensitivity.
3. Define record identity, schema, timestamp rules, and historical correction behavior.
4. Replace `fetch()` in `src/collector/collect.py` with read-only source retrieval.
5. Replace `parse()` with strict validation and domain normalization.
6. Replace the measurements schema in `migrations/0001_initial.sql` for a fresh template.
7. Replace `write_measurements()` in `src/collector/database.py` with the domain write policy.
8. Replace the snapshot watermark with explicit cursor or window rules if the source requires incremental collection.
9. Update `src/collector/config.py`, `config.example.toml`, and `.env.example` for source-specific requirements.
10. Choose a unique launchd label and collection interval.
11. Replace the fixture with sanitized representative source data.
12. Update tests for the new schema, source behavior, and checkpoint rules.
13. Run `uv run python -m pytest` without network access.
14. With operator permission, run one bounded live collection.
15. Verify idempotency, rollback, replay, schema drift, and interrupted-run recovery.
16. On macOS, install the agent only after the manual run succeeds.
17. Verify a real scheduled run and its database and log records.
18. Document backup, retention, and source-specific recovery in the README.

## Reliability rules

- Persist exact source bytes before parsing.
- Use stable record keys and a documented write policy.
- Keep SQLite writes, checkpoint changes, and successful run status in one transaction.
- Never commit inside the domain write function.
- Keep replay independent of source access and checkpoint advancement.
- Hold the kernel lock across migrations and the complete collection lifecycle.
- Never remove the lock file while another process can hold it open.
- Enforce request timeouts, response-size limits, bounded pagination, and source-specific rate limits.
- Retry only safe, read-only requests and transient errors.
- Raise `SchemaError` rather than silently ignoring invalid records.
- Add a migration instead of editing an already applied migration.
- Increment `PARSER_VERSION` after parser behavior changes.

## Credential and scheduling rules

- Never put credentials in the plist, URL, raw filename, logs, database metadata, or committed fixtures.
- Preserve the restricted environment-file parser. Do not source `.env` as shell code.
- Use a private environment file and read-only source credentials.
- Use absolute paths in the generated LaunchAgent.
- Never install a schedule from initialization or collection.
- Keep collection independent of launchd so manual runs and tests use the same code.
- Do not claim that launchd guarantees execution during sleep or logout.

## Required acceptance tests

Tests must cover repeat collection, corrected values, partial-write rollback, offline replay, schema drift, and interrupted-run recovery.
Test authentication failures, retry exhaustion, timeouts, and response limits for an HTTP source.
For paginated sources, test failure between pages and safe checkpoint recovery.
Validate generated plist paths, labels, intervals, credential exclusion, and install/uninstall behavior.
Use mocked service operations in automated tests. Reserve real launchd installation for an approved macOS acceptance run.
