"""All paths resolve from the configuration file, including under launchd."""
from dataclasses import dataclass
import math
import os
from pathlib import Path
import re
import tomllib
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Config:
    path: Path
    data_dir: Path
    fixture: Path
    url: str
    timeout_seconds: float
    max_response_bytes: int
    retries: int
    interval_seconds: int
    label: str

    @property
    def root(self) -> Path:
        return self.path.parent


def load(path: Path = Path("config.toml")) -> Config:
    path = path.expanduser().resolve()
    raw = tomllib.loads(path.read_text())
    defaults = dict(data_directory="var", fixture="tests/fixtures/measurements.json", url="",
                    timeout_seconds=30, max_response_bytes=1048576, retries=2,
                    interval_seconds=3600, label="local.collector")
    if set(raw) - set(defaults):
        raise ValueError("Unknown configuration key")
    values = defaults | raw
    for name in ("data_directory", "fixture", "url", "label"):
        if not isinstance(values[name], str):
            raise ValueError(f"Invalid {name}")
    for name in ("data_directory", "fixture"):
        if not values[name]:
            raise ValueError(f"Invalid {name}")
    for name, minimum in (("timeout_seconds", 0.001), ("max_response_bytes", 1), ("retries", 0), ("interval_seconds", 1)):
        value = values[name]
        types = (int, float) if name == "timeout_seconds" else (int,)
        if type(value) not in types or not math.isfinite(value) or value < minimum:
            raise ValueError(f"Invalid {name}")
    if values["retries"] > 10:
        raise ValueError("retries must not exceed 10")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9.-]{0,127}", values["label"]):
        raise ValueError("Invalid launchd label")
    if values["url"]:
        url = urlsplit(values["url"])
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.fragment:
            raise ValueError("Source URL must be HTTPS without credentials or a fragment")
    return Config(path, (path.parent / values["data_directory"]).resolve(),
                  (path.parent / values["fixture"]).resolve(), values["url"],
                  values["timeout_seconds"], values["max_response_bytes"],
                  values["retries"], values["interval_seconds"], values["label"])


def load_environment(root: Path) -> None:
    """Read a deliberately small KEY=value format, never shell code."""
    path = root / ".env"
    if not path.exists():
        return
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
        raise ValueError(".env must be a private regular file (chmod 600)")
    # An existing file is authoritative, including when it omits the token.
    os.environ.pop("COLLECTOR_TOKEN", None)
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or key != "COLLECTOR_TOKEN":
            raise ValueError(".env supports only COLLECTOR_TOKEN=value")
        os.environ[key] = value
