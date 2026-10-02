# SQLite-backed launchd collector template

This repository is one complete collection job for an agent to copy and modify.
It is not a plugin framework. The source, parser, schema, and schedule are explicit files.

The example collects timestamped station temperatures into SQLite.
By default, it reads a local JSON fixture without credentials or network access.
An optional HTTPS source demonstrates bounded reads, request timeouts, bearer authentication, and retries.

## Install and run

1. Install Python 3.12 and `uv`.
2. Change to the repository directory.
3. Install the dependencies:

   ```bash
   uv sync --locked
   ```

4. Copy the example configuration:

   ```bash
   cp config.example.toml config.toml
   cp .env.example .env
   chmod 600 .env
   ```

5. Initialize the database:

   ```bash
   uv run collector init
   ```

6. Collect one snapshot:

   ```bash
   uv run collector collect
   ```

7. Inspect the run records:

   ```bash
   uv run collector status --json
   ```

All configuration paths resolve from `config.toml`, not the current directory.
Keep the configuration in the repository root because migrations and launchd resources also resolve from that directory.
An alternate configuration path uses `collector --config /absolute/repository/config.toml collect`.

## Schedule on macOS

1. Run a successful manual collection before scheduling.
2. Set `interval_seconds` and a unique `label` in `config.toml`.
3. Install the user LaunchAgent:

   ```bash
   uv run collector launchd install
   ```

4. Inspect launchd:

   ```bash
   uv run collector launchd status
   ```

5. To stop scheduled collection, uninstall the agent:

   ```bash
   uv run collector launchd uninstall
   ```

Installation creates `~/Library/LaunchAgents/<label>.plist` and loads it into the current user's GUI domain.
It does not run the collector immediately. The first scheduled run occurs after the configured interval.
Missed intervals do not form a durable queue. Snapshot collection retrieves the latest available source data.

The generated plist contains absolute paths to the repository, virtual-environment Python, wrapper, configuration, and logs.
It contains no credential values. The wrapper reads no shell configuration and clears inherited collector credentials.
Python loads `.env` with a restricted `COLLECTOR_TOKEN=value` format. The file must have private permissions.

Keep the repository and `.venv` at stable paths. After moving either location, reinstall the agent.
After changing the label, uninstall the old label before installing the new one.
After changing the schedule, reinstall the agent.
Use a location outside macOS-protected Desktop and Documents folders unless access permissions explicitly allow it.
A logged-out or sleeping Mac does not guarantee timely collection.

## HTTP source

Set `url` to an HTTPS endpoint that returns this JSON schema:

```json
[
  {
    "station": "north",
    "measured_at": "2026-01-01T00:00:00Z",
    "temperature_c": 12.5
  }
]
```

For bearer authentication, set `COLLECTOR_TOKEN` in the private `.env` file.
Do not put secrets in the URL or configuration. The HTTP path makes only GET requests and never follows redirects.
It retries transport errors, HTTP 429, and selected server errors with bounded exponential delays.
Authentication and schema errors do not trigger retries.

`timeout_seconds` limits HTTP connect/read/write/pool inactivity, not total run duration.
`max_response_bytes` limits decoded payload bytes. The collector also checks fixture and replay sizes.
The example does not implement pagination, server-specific quotas, or a hard process deadline.
Add these rules when the real source requires them.

## Data and recovery

Runtime data lives under `var/` by default:

| Path | Contents |
| --- | --- |
| `collector.sqlite3` | Measurements, run records, retrievals, migrations, and checkpoint |
| `raw/` | Exact source bytes in immutable gzip files |
| `quarantine/` | References to raw responses with invalid schemas |
| `logs/collector.jsonl` | Structured lifecycle events |
| `logs/launchd.*.log` | Scheduled stdout and stderr |
| `collector.lock` | Persistent file for the kernel lock |

Each retrieval records its checksum, size, timestamp, and content type.
Each run records its parser version, outcome, record count, and retrieval ID.
Logs and run records contain error classes, not upstream exception messages that can expose credentials.

The collector normalizes timestamps to UTC and upserts by station and timestamp.
Historical corrections replace the value for that key. This example does not preserve linked revisions.
Duplicate keys inside one response cause quarantine rather than an arbitrary overwrite.

Raw data persists before parsing. Measurements, checkpoint, and successful run status commit in one transaction.
The checkpoint records the newest measurement timestamp. This snapshot collector rereads the entire response rather than using a pagination cursor.

If parsing fails, correct the parser or source schema before replay.
Find the retrieval ID in `collector status --json`, then run:

```bash
uv run collector replay <retrieval-id>
```

Replay verifies the checksum and uses the current parser and write rules.
It does not access the source or change the checkpoint.

If a write fails, the transaction rolls back the measurements and checkpoint together.
Run `collector collect` again after correcting the fault.
If collection overlaps, the new scheduled invocation exits successfully with a skip message.

After a process crash, the kernel releases the lock automatically.
The next collection marks stale `running` rows as `interrupted` before retrying the snapshot.
Do not remove the lock file while a process can hold it open.
A crash between raw storage and retrieval registration can leave an unreferenced raw file.

## Backup and retention

1. Uninstall the schedule.
2. Wait for active collection to finish.
3. Create a SQLite backup:

   ```bash
   sqlite3 var/collector.sqlite3 '.backup /absolute/backup/collector.sqlite3'
   ```

4. Copy `var/raw/` and `var/quarantine/` to the same backup.
5. Record checksums for the backup files.
6. Install the schedule again.

Before restoring, stop collection and preserve the damaged runtime directory.
Restore the database and raw files from the same backup.
Validate their checksums before collection or replay.

Raw payloads can contain secrets or personal data. Define access, encryption, retention, and deletion rules before live collection.
The template does not delete raw data or rotate logs automatically.

## Adapt and test

See [AGENTS.md](AGENTS.md) for the exact adaptation procedure.

```bash
uv run python -m pytest
```

Tests use local fixtures and mocked HTTP. Network access is blocked by default.
They cover duplicate runs, rollback, replay, quarantine, retries, limits, migrations, locks, credentials, and launchd configuration.
CI runs on Linux and macOS. Automated launchd tests mock service operations and do not install an actual user agent.

This is a breaking replacement of the earlier market-data application and generic framework.
Old commands and database schemas are not supported or migrated.
Use this repository checkout as the template. A standalone wheel does not include the root migrations and launchd resources.
