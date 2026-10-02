import pytest
from collector import database
from collector.config import load, load_environment
import os


def test_migration_checksum_and_atomicity(config):
    db = database.connect(config.data_dir)
    try:
        directory = config.root / "migrations"
        database.migrate(db, directory)
        database.migrate(db, directory)
        assert db.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == 1
        bad = directory / "0002_bad.sql"
        bad.write_text("CREATE TABLE partial(id INTEGER); INVALID SQL;")
        with pytest.raises(Exception):
            database.migrate(db, directory)
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='partial'").fetchone()
        bad.unlink()
        initial = directory / "0001_initial.sql"
        initial.write_text(initial.read_text() + "\n-- changed")
        with pytest.raises(ValueError, match="checksum"):
            database.migrate(db, directory)
    finally:
        db.close()


@pytest.mark.parametrize("extra", ['retries = -1', 'interval_seconds = 0', 'timeout_seconds = nan', 'unknown = 1', 'label = "../escape"', 'url = "http://example.test"'])
def test_invalid_configuration(config, extra):
    config.path.write_text(extra)
    with pytest.raises(ValueError):
        load(config.path)


def test_environment_private_and_authoritative(config, monkeypatch):
    path = config.root / ".env"
    path.write_text("COLLECTOR_TOKEN=file-token\n")
    path.chmod(0o644)
    with pytest.raises(ValueError, match="private"):
        load_environment(config.root)
    path.chmod(0o600)
    monkeypatch.setenv("COLLECTOR_TOKEN", "inherited-token")
    load_environment(config.root)
    assert os.environ["COLLECTOR_TOKEN"] == "file-token"
    path.write_text("# no token\n")
    load_environment(config.root)
    assert "COLLECTOR_TOKEN" not in os.environ
