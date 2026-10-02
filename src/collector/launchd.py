"""User LaunchAgent installation. No root privileges or implicit scheduling."""
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile

from .collect import initialize
from .config import Config, load_environment


def domain() -> str:
    return f"gui/{os.getuid()}"


def require_macos():
    if sys.platform != "darwin":
        raise RuntimeError("launchd commands require macOS")


def plist_path(config: Config) -> Path:
    return Path.home() / "Library/LaunchAgents" / (config.label + ".plist")


def render(config: Config, python: Path | None = None) -> bytes:
    interpreter = (python or Path(sys.executable)).absolute()
    wrapper = config.root / "launchd/run-with-env.sh"
    if not interpreter.is_file() or not wrapper.is_file():
        raise ValueError("Interpreter or launchd wrapper is missing")
    with (config.root / "launchd/collector.plist.template").open("rb") as stream:
        value = plistlib.load(stream)
    value.update(Label=config.label,
                 ProgramArguments=["/bin/sh", str(wrapper), str(interpreter), str(config.path)],
                 WorkingDirectory=str(config.root), StartInterval=config.interval_seconds,
                 StandardOutPath=str(config.data_dir / "logs/launchd.stdout.log"),
                 StandardErrorPath=str(config.data_dir / "logs/launchd.stderr.log"))
    return plistlib.dumps(value)


def call(*arguments, check=True):
    return subprocess.run(["/bin/launchctl", *arguments], check=check, capture_output=True, text=True)


def install(config: Config):
    require_macos()
    load_environment(config.root)
    initialize(config)
    body = render(config)
    (config.data_dir / "logs").mkdir(parents=True, exist_ok=True)
    path = plist_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    target = domain() + "/" + config.label
    if call("print", target, check=False).returncode == 0:
        call("bootout", target)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    call("enable", target)
    call("bootstrap", domain(), str(path))
    return path


def uninstall(config: Config):
    require_macos()
    target = domain() + "/" + config.label
    if call("print", target, check=False).returncode == 0:
        call("bootout", target)
    plist_path(config).unlink(missing_ok=True)


def status(config: Config):
    require_macos()
    result = call("print", domain() + "/" + config.label, check=False)
    return result.stdout if result.returncode == 0 else "Not loaded"
