from pathlib import Path
import plistlib
from types import SimpleNamespace

import pytest
from collector import launchd


def test_generated_plist(config):
    body = launchd.render(config)
    value = plistlib.loads(body)
    assert value["Label"] == config.label
    assert value["StartInterval"] == config.interval_seconds
    assert value["RunAtLoad"] is False
    assert value["Umask"] == 0o077
    assert value["WorkingDirectory"] == str(config.root)
    assert all(Path(arg).is_absolute() for arg in value["ProgramArguments"])
    assert "EnvironmentVariables" not in value
    assert b"COLLECTOR_TOKEN" not in body
    assert Path(value["StandardErrorPath"]).is_absolute()


def test_install_uninstall_and_status(config, monkeypatch, tmp_path):
    monkeypatch.setattr(launchd, "require_macos", lambda: None)
    path = tmp_path / "agents/local.collector.plist"
    monkeypatch.setattr(launchd, "plist_path", lambda _: path)
    calls = []
    def call(*arguments, **kwargs):
        calls.append(arguments)
        return SimpleNamespace(returncode=0, stdout="loaded")
    monkeypatch.setattr(launchd, "call", call)
    assert launchd.install(config) == path
    assert path.exists()
    assert path.stat().st_mode & 0o077 == 0
    assert ("bootout", launchd.domain() + "/" + config.label) in calls
    assert calls[-1] == ("bootstrap", launchd.domain(), str(path))
    assert launchd.status(config) == "loaded"
    launchd.uninstall(config)
    assert not path.exists()
    assert (config.data_dir / "collector.sqlite3").exists()


def test_wrapper_clears_inherited_credentials(config):
    import os
    import subprocess
    interpreter = config.root / "fake-python"
    interpreter.write_text('#!/bin/sh\nprintf "%s" "${COLLECTOR_TOKEN-unset}"\n')
    interpreter.chmod(0o700)
    result = subprocess.run(
        ["/bin/sh", str(config.root / "launchd/run-with-env.sh"), str(interpreter), str(config.path)],
        env=dict(os.environ, COLLECTOR_TOKEN="inherited-secret"),
        capture_output=True, text=True, check=True,
    )
    assert result.stdout == "unset"


def test_platform_gate(config, monkeypatch):
    monkeypatch.setattr(launchd.sys, "platform", "linux")
    with pytest.raises(RuntimeError, match="macOS"):
        launchd.install(config)
