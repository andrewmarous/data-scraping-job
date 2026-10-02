import gzip
import json
import logging
import os
import shutil
from pathlib import Path

import pytest

from market_data.config import ConfigError, load_env_file
from market_data.database import connect, migrate
from market_data.errors import MarketDataError
from market_data.logging import JsonFormatter, configure_logging
from market_data.raw_store import quarantine, store_raw


MIGRATIONS = Path(__file__).parents[1] / "migrations"


def test_all_source_schemas_and_migration_ledger_are_created(tmp_path):
    database = tmp_path / "market.sqlite3"
    assert migrate(database, MIGRATIONS) == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
    connection = connect(database)
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"collection_runs", "raw_retrievals", "openrouter_daily_tokens", "vast_offer_snapshots", "aws_spot_prices", "gpu_instance_catalog", "aws_collection_coverage", "provider_market_observations"} <= tables
    assert [row[0] for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")] == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]


def test_changed_applied_migration_is_rejected(tmp_path):
    migrations = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS, migrations)
    database = tmp_path / "market.sqlite3"
    migrate(database, migrations)
    with (migrations / "0002_source_schemas.sql").open("a") as stream:
        stream.write("\n-- changed\n")
    with pytest.raises(MarketDataError, match="checksum changed"):
        migrate(database, migrations)


def test_raw_archive_is_exact_reproducible_and_quarantinable(tmp_path):
    body = b'{"source":"native","value":1}'
    first = store_raw(tmp_path, "test", "2026-08-31T12:00:00Z", "run-a", body)
    second = store_raw(tmp_path, "test", "2026-08-31T12:00:00Z", "run-b", body)
    assert gzip.decompress(first.path.read_bytes()) == body
    assert first.sha256 == second.sha256
    # Fixed gzip metadata makes equal bodies produce equal compressed bytes.
    assert first.path.read_bytes() == second.path.read_bytes()
    quarantined = quarantine(tmp_path, "test", first.path)
    assert quarantined.read_bytes() == first.path.read_bytes()


def test_json_logging_redacts_credentials(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "super-private-value")
    record = logging.LogRecord("market_data", logging.ERROR, "", 0, "Authorization: Bearer super-private-value", (), None)
    output = JsonFormatter().format(record)
    assert "super-private-value" not in output
    assert "REDACTED" in output
    assert json.loads(output)["level"] == "ERROR"


def test_json_logging_redacts_provider_credentials(monkeypatch):
    monkeypatch.setenv("SALAD_API_KEY", "salad-private-value")
    output = JsonFormatter().format(logging.LogRecord("market_data", logging.ERROR, "", 0, "salad-private-value", (), None))
    assert "salad-private-value" not in output


def test_log_rotation_and_retention(tmp_path):
    directory=tmp_path/"logs"; directory.mkdir(); current=directory/"market-data.jsonl"
    current.write_bytes(b"x"*(10*1024*1024)); old=directory/"market-data.jsonl.20000101T000000Z"; old.write_text("old")
    os.utime(old,(0,0)); configure_logging(tmp_path,"INFO",30)
    assert not old.exists() and list(directory.glob("market-data.jsonl.20*"))


def test_environment_file_must_be_private(tmp_path):
    path = tmp_path / ".env"
    path.write_text("OPENROUTER_API_KEY=value\n")
    path.chmod(0o644)
    with pytest.raises(ConfigError, match="mode 0600"):
        load_env_file(path)
    path.chmod(0o600)
    assert load_env_file(path) == {"OPENROUTER_API_KEY": "value"}
