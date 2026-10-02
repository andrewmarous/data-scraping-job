# Operational acceptance record

Date: 2026-09-01 UTC

## Automated checks

The offline suite contains 34 tests. All 34 tests passed.

The suite covers these behaviors:

- ordered migrations and migration checksums;
- SQLite safety options and foreign keys;
- immutable raw files and quarantine files;
- secret removal and `.env` permissions;
- active and stale locks;
- OpenRouter retries, revisions, and atomic quarantine;
- Vast contract parsing and rate-limit retries;
- AWS region discovery, pagination, catalog parsing, and raw archival;
- independent `launchd` jobs; and
- local quality and comparison views.

## OpenRouter

The `launchd` job finished with exit code 0. It parsed 204 rows in the overlap window.

A second overlap run inserted no rows. This result proves idempotent normalization.

The historical collection contains 606 days. The first day is 2025-01-01.
The last day is 2026-08-31. The table contains 30,906 current rows.

## Vast.ai

Each Vast contract has a separate `launchd` job. All three jobs finished with exit code 0.

Each job parsed and inserted 512 offers. Each normalized offer has a raw retrieval record.

The jobs call only `POST /api/v0/bundles/`. The collector contains no rental or bid operation.

## AWS Spot

The AWS `launchd` job finished successfully in all 17 enabled regions.
The run parsed 5,644 source observations and stored 68 region-family coverage rows.

The database contains 5,829 unique Spot observations. All observations join to a catalog version.

A repeated all-region 48-hour run parsed 5,644 rows and inserted no rows.
This result proves overlap deduplication for the full schedule.

The collector stored offering discovery, instance descriptions, and Spot history as raw evidence.

The configured AWS profile did not pass the least-privilege check.
`RunInstances --dry-run` returned `DryRunOperation`, which proves that the profile permits instance launches.
The dry-run did not launch an instance.

Replace the profile with an identity that denies `ec2:RunInstances`.
Then refresh its AWS SSO session:

```bash
uv run --env-file .env sh -c 'aws sso login --profile "$AWS_PROFILE"'
```

Then start the installed AWS job:

```bash
launchctl kickstart -k gui/$(id -u)/local.market-data.aws-spot
```

## Reports

The local reports show freshness, completeness, revisions, quarantine counts, failures, and unmapped values.

JSONL output includes the generation time and mapping version. CSV output preserves source fields and package fields.

The cross-cloud view keeps the complete-node price, GPU count, topology, contract, currency, term, and revocability.
