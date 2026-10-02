import sqlite3
from pathlib import Path

import pytest

from market_data.database import connect, migrate
from market_data.errors import MarketDataError


def migrations(): return Path(__file__).parents[1] / "migrations"

def test_migration_is_idempotent_and_enables_safety_pragmas(tmp_path):
    database = tmp_path / "test.sqlite3"
    assert migrate(database, migrations()) == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
    assert migrate(database, migrations()) == []
    connection = connect(database)
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"

def test_foreign_key_is_enforced(tmp_path):
    database = tmp_path / "test.sqlite3"; migrate(database, migrations())
    connection = connect(database)
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("INSERT INTO raw_retrievals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", tuple(str(i) for i in range(17)))
