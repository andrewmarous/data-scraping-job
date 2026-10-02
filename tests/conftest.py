from pathlib import Path
import shutil
import socket

import pytest
from collector.config import load

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Tests must not access the network")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.delenv("COLLECTOR_TOKEN", raising=False)


@pytest.fixture
def config(tmp_path):
    for directory in ("migrations", "launchd"):
        shutil.copytree(ROOT / directory, tmp_path / directory)
    (tmp_path / "tests/fixtures").mkdir(parents=True)
    shutil.copy(ROOT / "tests/fixtures/measurements.json", tmp_path / "tests/fixtures/measurements.json")
    shutil.copy(ROOT / "config.example.toml", tmp_path / "config.toml")
    return load(tmp_path / "config.toml")
