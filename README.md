# Local market-data collector

This application collects OpenRouter token totals and GPU market data on one computer. GPU sources include AWS, Vast.ai, Runpod, Gcore, CLORE.AI, SaladCloud, Nebius, Azure, Google Cloud, and CoreWeave.

See [`docs/provider-source-setup.md`](docs/provider-source-setup.md) for provider scope and the final credential to-do list.

See [`RELEASE_STATUS.md`](RELEASE_STATUS.md) for implementation status. Vast.ai offer and AWS Spot-price collectors are available. Release 4 source decisions are documented in [`docs/release-4-source-verification.md`](docs/release-4-source-verification.md).

The application stores normalized observations in SQLite. It also stores each unchanged source response as an immutable gzip file.

## Install

1. Install Python 3.12 and `uv`.
2. Change to this directory.
3. Install the application:

   ```bash
   uv sync
   ```

4. Copy the example files:

   ```bash
   cp config.example.toml config.toml
   cp .env.example .env
   chmod 600 .env
   ```

5. Put an OpenRouter API key in `.env`.
6. Initialize the application:

   ```bash
   uv run --env-file .env market-data init
   uv run --env-file .env market-data verify-config
   ```

7. Collect the available history:

   ```bash
   uv run --env-file .env market-data backfill openrouter --start 2025-01-01
   ```

8. On macOS, install one agent for every enabled source:

   ```bash
   uv run --env-file .env market-data launchd install
   uv run --env-file .env market-data status
   ```

## Data locations

The default configuration stores runtime data under `var/`:

- `var/market-data.sqlite3` contains normalized data and run records.
- `var/raw/openrouter/` contains immutable source responses.
- `var/raw/vast/` and `var/raw/aws_spot/` contain source and discovery responses.
- `var/raw/<provider>/` contains responses from each additional provider.
- `var/quarantine/openrouter/` contains responses that fail schema validation.
- `var/logs/` contains JSONL and `launchd` logs.
- `var/locks/` contains the active source lock.

The application resolves relative paths from `config.toml`. It does not resolve these paths from the current directory.

## Operations

Run one overlapping daily collection:

```bash
uv run --env-file .env market-data collect openrouter
```

Retry a specific interval:

```bash
uv run --env-file .env market-data collect openrouter --start 2026-08-01 --end 2026-08-03
```

The command is idempotent. A changed historical value creates a linked revision.

Collect Vast offers (all configured contracts or one contract):

```bash
uv run --env-file .env market-data collect vast
uv run --env-file .env market-data collect vast --contract interruptible
```

Collect GPU Spot prices from every enabled AWS region. Leave `aws_spot.regions = []` to enable region discovery:

```bash
uv run --env-file .env market-data collect aws-spot
```

A region that requires account opt-in is collected only after that region is enabled for the configured AWS account. Use `--region us-east-1` for a bounded run.

Use the policy in `docs/aws-market-data-readonly-policy.json` for the AWS identity.
Do not attach `AmazonEC2FullAccess` or another write policy.

Set `AWS_PROFILE` in `.env` to the read-only profile name. Then refresh AWS SSO:

```bash
uv run --env-file .env sh -c 'aws sso login --profile "$AWS_PROFILE"'
```

Use `RunInstances --dry-run` to make sure that the profile returns `UnauthorizedOperation`.
A `DryRunOperation` result means that the profile can launch instances and is not acceptable.

Show machine-readable status:

```bash
uv run --env-file .env market-data status --json
```

Collect one additional provider:

```bash
uv run --env-file .env market-data collect provider runpod
```

Export approved local views as JSONL or CSV. JSONL includes generation time and mapping-version metadata:

```bash
uv run --env-file .env market-data report query vast_offer_depth
uv run --env-file .env market-data report query aws_spot_per_gpu --format csv --output spot.csv
```

Show the Release 4 source-verification gate:

```bash
uv run --env-file .env market-data indexes status
```

Apply application updates and migrations:

```bash
uv sync
uv run --env-file .env market-data migrate
uv run --env-file .env market-data verify-config
```

Change a credential by editing `.env`. Keep the file mode at `0600`.

Unload the scheduled agent without deleting data:

```bash
uv run --env-file .env market-data launchd uninstall
```

Set each source's `enabled = false`, then reinstall the agents, to disable collection without deleting data.

Inspect the agent on macOS:

```bash
launchctl print gui/$(id -u)/local.market-data.openrouter
launchctl print gui/$(id -u)/local.market-data.vast.interruptible
launchctl print gui/$(id -u)/local.market-data.aws-spot
```

## Backup

1. Stop the scheduled agent.
2. Checkpoint the database:

   ```bash
   sqlite3 var/market-data.sqlite3 'PRAGMA wal_checkpoint(TRUNCATE);'
   ```

3. Copy the database and raw directory to the backup location:

   ```bash
   cp var/market-data.sqlite3 /path/to/backup/
   cp -R var/raw /path/to/backup/
   ```

4. Record checksums:

   ```bash
   find /path/to/backup -type f -print0 | xargs -0 shasum -a 256 > /path/to/backup/SHA256SUMS
   ```

5. Install the scheduled agent again.

## Restore

1. Unload the scheduled agent.
2. Move the damaged `var/` directory to a safe location.
3. Create a new `var/` directory.
4. Copy the database and raw directory from the backup.
5. Compare the files with `SHA256SUMS`.
6. Run `uv run --env-file .env market-data migrate`.
7. Run `uv run --env-file .env market-data status`.
8. Install the scheduled agent again.

## Safety and scope

The OpenRouter collector only calls the rankings endpoint and does not make inference requests. The Vast collector only searches offers; it never rents or bids. The AWS collector invokes read-only EC2 region, offering, and Spot Price History operations; it never launches instances.

The application never stores the bearer token in request metadata, raw paths, SQLite, logs, fixtures, or `launchd` property lists.

Tests do not use the network by default. Run them with `uv run pytest`.

The installed wrapper passes `.env` to `uv`. It removes inherited credential variables first.
This behavior makes terminal and `launchd` credential selection identical.
