from dataclasses import replace
import gzip
import importlib
import json
import sqlite3

import httpx
import pytest
from typer.testing import CliRunner

from collector.cli import app
from collector.collect import collect, fetch, parse, SchemaError, RetryableError
from collector.locking import AlreadyRunning, lock
from collector import raw_store

module = importlib.import_module("collector.collect")


def rows(config, table):
    db = sqlite3.connect(config.data_dir / "collector.sqlite3")
    try:
        return db.execute(f"SELECT * FROM {table}").fetchall()
    finally:
        db.close()


def test_repeat_and_replay(config, monkeypatch):
    collect(config)
    collect(config)
    assert len(rows(config, "measurements")) == 2
    checkpoint = rows(config, "checkpoint")
    retrieval = rows(config, "retrievals")[0][0]
    monkeypatch.setattr(module, "fetch", lambda _: pytest.fail("Replay fetched data"))
    collect(config, retrieval)
    assert rows(config, "checkpoint") == checkpoint
    assert rows(config, "runs")[-1][-1] == 1


def test_correction_and_watermark(config):
    collect(config)
    checkpoint = rows(config, "checkpoint")
    values = json.loads(config.fixture.read_text())
    values[0]["temperature_c"] = 99.5
    config.fixture.write_text(json.dumps(values))
    collect(config)
    assert len(rows(config, "measurements")) == 2
    assert rows(config, "measurements")[0][2] == 99.5
    values[0]["measured_at"] = "2025-01-01T00:00:00Z"
    config.fixture.write_text(json.dumps(values[:1]))
    collect(config)
    assert rows(config, "checkpoint") == checkpoint


def test_schema_drift(config):
    config.fixture.write_text('[{"unexpected": 1}]')
    with pytest.raises(SchemaError):
        collect(config)
    assert rows(config, "runs")[0][3] == "quarantined"
    assert not rows(config, "checkpoint")
    assert len(list((config.data_dir / "quarantine").glob("*.json"))) == 1
    retrieval = rows(config, "retrievals")[0]
    with gzip.open(config.data_dir / retrieval[3]) as stream:
        assert stream.read() == config.fixture.read_bytes()


def test_write_rollback_and_secret_redaction(config, monkeypatch):
    def fail(db, records, retrieval_id):
        db.execute("INSERT INTO measurements VALUES('partial','date',1,?)", (retrieval_id,))
        raise RuntimeError("secret-token")
    monkeypatch.setattr(module.database, "write_measurements", fail)
    with pytest.raises(RuntimeError):
        collect(config)
    assert not rows(config, "measurements")
    assert not rows(config, "checkpoint")
    assert "secret-token" not in str(rows(config, "runs"))
    assert "secret-token" not in (config.data_dir / "logs/collector.jsonl").read_text()


def test_overlap_and_crash_recovery(config):
    with lock(config.data_dir):
        with pytest.raises(AlreadyRunning):
            collect(config)
    collect(config)
    db = sqlite3.connect(config.data_dir / "collector.sqlite3")
    db.execute("UPDATE runs SET status='running'")
    db.commit()
    db.close()
    collect(config)
    assert rows(config, "runs")[0][3] == "interrupted"


def test_kernel_releases_lock_after_process_death(config):
    import subprocess
    import sys
    script = (
        "import sys,time; from pathlib import Path; from collector.locking import lock; "
        "guard=lock(Path(sys.argv[1])); guard.__enter__(); "
        "print('locked',flush=True); time.sleep(60)"
    )
    import os
    from pathlib import Path
    source = str(Path(module.__file__).resolve().parents[1])
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(config.data_dir)],
        env=dict(os.environ, PYTHONPATH=source), stdout=subprocess.PIPE, text=True,
    )
    try:
        assert process.stdout.readline().strip() == "locked"
        with pytest.raises(AlreadyRunning):
            collect(config)
    finally:
        process.kill()
        process.wait(timeout=5)
        process.stdout.close()
    collect(config)
    assert len(rows(config, "measurements")) == 2


def test_limits_and_corrupt_raw(config):
    with pytest.raises(ValueError, match="byte limit"):
        collect(replace(config, max_response_bytes=1))
    collect(config)
    retrieval = rows(config, "retrievals")[0]
    with gzip.open(config.data_dir / retrieval[3], "wb") as stream:
        stream.write(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        collect(config, retrieval[0])


@pytest.mark.parametrize("body", [b'{}', b'not json', b'[null]',
    b'[{"station":"a","measured_at":"2026-01-01","temperature_c":1}]',
    b'[{"station":"a","measured_at":"2026-01-01T00:00:00Z","temperature_c":NaN}]'])
def test_bad_records(body):
    with pytest.raises(SchemaError):
        parse(body)


def test_utc_identity_and_duplicates():
    value = dict(station="a", measured_at="2026-01-01T01:00:00+01:00", temperature_c=1)
    assert parse(json.dumps([value]).encode())[0][1] == "2026-01-01T00:00:00.000000Z"
    with pytest.raises(SchemaError):
        parse(json.dumps([value, value]).encode())


def mock_client(monkeypatch, handler):
    client_type = httpx.Client
    monkeypatch.setattr(module.httpx, "Client", lambda **kwargs: client_type(transport=httpx.MockTransport(handler), **kwargs))
    monkeypatch.setattr(module.time, "sleep", lambda _: None)


def test_http_retry_and_auth(config, monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(503 if len(calls) == 1 else 200, content=config.fixture.read_bytes())
    mock_client(monkeypatch, handler)
    monkeypatch.setenv("COLLECTOR_TOKEN", "secret-token")
    body, _ = fetch(replace(config, url="https://example.test/data"))
    assert len(calls) == 2
    assert calls[0].headers["authorization"] == "Bearer secret-token"
    assert body == config.fixture.read_bytes()


def test_http_timeout_retries(config, monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("secret in upstream error", request=request)
    mock_client(monkeypatch, handler)
    with pytest.raises(httpx.ReadTimeout):
        fetch(replace(config, url="https://example.test/data"))
    assert len(calls) == config.retries + 1


def test_retry_exhaustion_and_permanent_error(config, monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(429)
    mock_client(monkeypatch, handler)
    with pytest.raises(RetryableError):
        fetch(replace(config, url="https://example.test/data"))
    assert len(calls) == config.retries + 1


@pytest.mark.parametrize("status", [401, 302])
def test_no_retry_or_redirect(config, monkeypatch, status):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={"Location": "https://elsewhere.test"})
    mock_client(monkeypatch, handler)
    with pytest.raises(httpx.HTTPStatusError):
        fetch(replace(config, url="https://example.test/data"))
    assert len(calls) == 1


def test_http_byte_limit(config, monkeypatch):
    mock_client(monkeypatch, lambda _: httpx.Response(200, content=b"too large"))
    with pytest.raises(ValueError, match="byte limit"):
        fetch(replace(config, url="https://example.test/data", max_response_bytes=1))


def test_raw_cannot_overwrite(config):
    raw_store.store(config.data_dir, "unique", b"original")
    with pytest.raises(FileExistsError):
        raw_store.store(config.data_dir, "unique", b"replacement")


def test_cli(config):
    runner = CliRunner()
    prefix = ["--config", str(config.path)]
    for command in (["init"], ["collect"], ["status", "--json"]):
        result = runner.invoke(app, prefix + command)
        assert result.exit_code == 0, result.output
    assert len(json.loads(result.stdout)["runs"]) == 1
