from __future__ import annotations

import os
import stat
import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from .errors import ConfigError

_ALLOWED = {
    "data": {"directory", "raw_retention_days"},
    "logging": {"level", "retention_days"},
    "application": {"contact", "max_run_minutes"},
    "openrouter": {"enabled", "cadence", "overlap_days", "backfill_start", "request_timeout_seconds", "max_response_bytes"},
    "openrouter_batch": {"enabled"},
    "vast": {"enabled", "contracts", "bid_interval_minutes", "other_interval_minutes", "request_timeout_seconds", "max_response_bytes"},
    "aws_spot": {"enabled", "regions", "instance_families", "overlap_hours", "aws_profile"},
    "gpu_indexes": {"enabled_sources"},
    "provider_catalogs": {"enabled_sources", "interval_minutes", "request_timeout_seconds", "max_response_bytes", "gcore_region_ids", "gcore_project_id", "salad_organization"},
}


@dataclass(frozen=True)
class Config:
    path: Path
    data_dir: Path
    raw_retention_days: int
    log_level: str
    log_retention_days: int
    contact: str
    max_run_minutes: int
    openrouter_enabled: bool
    openrouter_batch_enabled: bool
    openrouter_overlap_days: int
    openrouter_backfill_start: date
    openrouter_timeout: float
    openrouter_max_bytes: int
    vast_enabled: bool
    vast_contracts: tuple[str, ...]
    vast_bid_interval_minutes: int
    vast_other_interval_minutes: int
    vast_timeout: float
    vast_max_bytes: int
    aws_spot_enabled: bool
    aws_regions: tuple[str, ...]
    aws_instance_families: tuple[str, ...]
    aws_overlap_hours: int
    aws_profile: str | None
    gpu_index_sources: tuple[str, ...]
    provider_catalog_sources: tuple[str, ...]
    provider_catalog_interval_minutes: int
    provider_catalog_timeout: float
    provider_catalog_max_bytes: int
    gcore_region_ids: tuple[str, ...]
    gcore_project_id: str | None
    salad_organization: str | None

    @property
    def database_path(self) -> Path:
        return self.data_dir / "market-data.sqlite3"


def _integer(section: dict[str, Any], key: str, minimum: int, default: int) -> int:
    value = section.get(key, default)
    if type(value) is not int or value < minimum:
        raise ConfigError(f"{key} must be an integer of at least {minimum}")
    return value


def _positive_number(section: dict[str, Any], key: str, default: float) -> float:
    value = section.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ConfigError(f"{key} must be positive")
    return float(value)


def _boolean(section: dict[str, Any], key: str, default: bool) -> bool:
    value = section.get(key, default)
    if type(value) is not bool:
        raise ConfigError(f"{key} must be true or false")
    return value


def _strings(section: dict[str, Any], key: str, default: list[str]) -> tuple[str, ...]:
    value = section.get(key, default)
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ConfigError(f"{key} must be a nonempty list of strings")
    if len(value) != len(set(value)):
        raise ConfigError(f"{key} must not contain duplicates")
    return tuple(value)


def load_config(path: str | Path | None = None) -> Config:
    default = Path(__file__).parents[2] / "config.toml"
    selected = Path(path or os.environ.get("MARKET_DATA_CONFIG", default)).expanduser().resolve()
    try:
        with selected.open("rb") as stream:
            raw = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"Cannot read configuration {selected}: {exc}") from exc
    unknown_sections = set(raw) - set(_ALLOWED)
    if unknown_sections:
        raise ConfigError(f"Unknown configuration section: {sorted(unknown_sections)[0]}")
    for name, values in raw.items():
        if not isinstance(values, dict):
            raise ConfigError(f"Configuration section {name} must be a table")
        unknown = set(values) - _ALLOWED[name]
        if unknown:
            raise ConfigError(f"Unknown configuration key: {name}.{sorted(unknown)[0]}")

    data, logging, app = raw.get("data", {}), raw.get("logging", {}), raw.get("application", {})
    openrouter, vast, aws = raw.get("openrouter", {}), raw.get("vast", {}), raw.get("aws_spot", {})
    gpu_indexes = raw.get("gpu_indexes", {})
    catalogs = raw.get("provider_catalogs", {})
    directory = data.get("directory", "var")
    if not isinstance(directory, str) or not directory:
        raise ConfigError("data.directory must be a nonempty string")
    if openrouter.get("cadence", "daily") != "daily":
        raise ConfigError("openrouter.cadence must be daily")
    try:
        backfill = date.fromisoformat(openrouter.get("backfill_start", "2025-01-01"))
    except (TypeError, ValueError) as exc:
        raise ConfigError("openrouter.backfill_start must be YYYY-MM-DD") from exc
    if backfill < date(2025, 1, 1):
        raise ConfigError("openrouter.backfill_start cannot precede 2025-01-01")
    level = logging.get("level", "INFO")
    if level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        raise ConfigError("logging.level is invalid")
    contact = app.get("contact", "local-operator")
    if not isinstance(contact, str) or not contact.strip():
        raise ConfigError("application.contact must be a nonempty string")
    contracts = _strings(vast, "contracts", ["on-demand", "interruptible", "reserved"])
    allowed_contracts = {"on-demand", "interruptible", "reserved"}
    if set(contracts) - allowed_contracts:
        raise ConfigError("vast.contracts contains an unverified contract")
    regions_value = aws.get("regions", [])
    if not isinstance(regions_value, list) or any(not isinstance(item, str) or not item.strip() for item in regions_value):
        raise ConfigError("aws_spot.regions must be a list of region names")
    if len(regions_value) != len(set(regions_value)):
        raise ConfigError("aws_spot.regions must not contain duplicates")
    profile = aws.get("aws_profile")
    if profile is not None and (not isinstance(profile, str) or not profile.strip()):
        raise ConfigError("aws_spot.aws_profile must be a nonempty string")
    gpu_index_sources = gpu_indexes.get("enabled_sources", [])
    if not isinstance(gpu_index_sources, list) or any(
        not isinstance(item, str) or not item.strip() for item in gpu_index_sources
    ):
        raise ConfigError("gpu_indexes.enabled_sources must be a list of source names")
    if len(gpu_index_sources) != len(set(gpu_index_sources)):
        raise ConfigError("gpu_indexes.enabled_sources must not contain duplicates")
    from .index_verification import eligible_sources
    blocked = set(gpu_index_sources) - eligible_sources()
    if blocked:
        raise ConfigError(
            "GPU index source has not passed the Release 4 verification gate: "
            + sorted(blocked)[0]
        )
    supported_catalogs = {"runpod", "gcore", "clore", "saladcloud", "nebius", "azure", "google_cloud", "coreweave", "lambda"}
    catalog_sources = catalogs.get("enabled_sources", [])
    if not isinstance(catalog_sources, list) or any(not isinstance(item, str) or item not in supported_catalogs for item in catalog_sources):
        raise ConfigError("provider_catalogs.enabled_sources contains an unsupported source")
    if len(catalog_sources) != len(set(catalog_sources)):
        raise ConfigError("provider_catalogs.enabled_sources must not contain duplicates")
    gcore_regions = catalogs.get("gcore_region_ids", [])
    if not isinstance(gcore_regions, list) or any(not isinstance(item, str) or not item for item in gcore_regions):
        raise ConfigError("provider_catalogs.gcore_region_ids must be a list of region IDs")
    gcore_project = catalogs.get("gcore_project_id")
    salad_org = catalogs.get("salad_organization")
    for key, value in (("gcore_project_id", gcore_project), ("salad_organization", salad_org)):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ConfigError(f"provider_catalogs.{key} must be a nonempty string")
    data_dir = Path(directory).expanduser()
    if not data_dir.is_absolute():
        data_dir = selected.parent / data_dir
    return Config(
        selected, data_dir.resolve(), _integer(data, "raw_retention_days", 0, 0), level,
        _integer(logging, "retention_days", 0, 30), contact, _integer(app, "max_run_minutes", 1, 30),
        _boolean(openrouter, "enabled", True), _boolean(raw.get("openrouter_batch", {}), "enabled", False), _integer(openrouter, "overlap_days", 0, 3), backfill,
        _positive_number(openrouter, "request_timeout_seconds", 30), _integer(openrouter, "max_response_bytes", 1, 52_428_800),
        _boolean(vast, "enabled", False), contracts, _integer(vast, "bid_interval_minutes", 1, 5),
        _integer(vast, "other_interval_minutes", 1, 10), _positive_number(vast, "request_timeout_seconds", 30),
        _integer(vast, "max_response_bytes", 1, 20_971_520),
        _boolean(aws, "enabled", False), tuple(regions_value),
        _strings(aws, "instance_families", ["g5", "g6", "p4", "p5"]), _integer(aws, "overlap_hours", 1, 48), profile,
        tuple(gpu_index_sources), tuple(catalog_sources), _integer(catalogs, "interval_minutes", 5, 60),
        _positive_number(catalogs, "request_timeout_seconds", 30), _integer(catalogs, "max_response_bytes", 1, 52_428_800),
        tuple(gcore_regions), gcore_project, salad_org,
    )


def load_env_file(path: Path) -> dict[str, str]:
    """Load recognized literal assignments from a private regular file."""
    if path.is_symlink():
        raise ConfigError(f"Credential file must not be a symlink: {path}")
    try:
        info = path.stat()
    except OSError as exc:
        raise ConfigError(f"Cannot read credential file {path}: {exc}") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise ConfigError(f"Credential file must be owned by this user and have mode 0600: {path}")
    allowed = {"OPENROUTER_API_KEY", "VAST_API_KEY", "AWS_PROFILE", "MARKET_DATA_CONFIG", "RUNPOD_API_KEY", "GCORE_API_KEY", "CLORE_API_KEY", "SALAD_API_KEY", "GOOGLE_CLOUD_API_KEY", "LAMBDA_API_KEY"}
    values: dict[str, str] = {}
    for number, raw_line in enumerate(path.read_text().splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ConfigError(f"Invalid credential assignment at line {number}")
        name, value = line.split("=", 1)
        if name not in allowed or not value or "\x00" in value or any(token in value for token in ("$(", "${", "`")):
            raise ConfigError(f"Invalid credential assignment at line {number}")
        values[name] = value
    return values


def verify_config(config: Config, *, require_credentials: bool = True) -> None:
    config.data_dir.mkdir(parents=True, exist_ok=True)
    if require_credentials and (config.openrouter_enabled or config.openrouter_batch_enabled) and not os.environ.get("OPENROUTER_API_KEY"):
        raise ConfigError("OPENROUTER_API_KEY is required when OpenRouter is enabled")
    if require_credentials and config.vast_enabled and not os.environ.get("VAST_API_KEY"):
        raise ConfigError("VAST_API_KEY is required when Vast is enabled")
    if require_credentials and config.aws_spot_enabled and not (os.environ.get("AWS_PROFILE") or config.aws_profile):
        raise ConfigError("AWS_PROFILE or aws_spot.aws_profile is required when AWS Spot is enabled")
    requirements = {"runpod":"RUNPOD_API_KEY", "lambda":"LAMBDA_API_KEY", "clore":"CLORE_API_KEY", "saladcloud":"SALAD_API_KEY", "google_cloud":"GOOGLE_CLOUD_API_KEY", "gcore":"GCORE_API_KEY"}
    for source in config.provider_catalog_sources:
        name = requirements.get(source)
        if require_credentials and name and not os.environ.get(name):
            raise ConfigError(f"{name} is required when {source} is enabled")
    if require_credentials and "gcore" in config.provider_catalog_sources and (not config.gcore_project_id or not config.gcore_region_ids or config.gcore_project_id.startswith("REPLACE_") or any(region.startswith("REPLACE_") for region in config.gcore_region_ids)):
        raise ConfigError("Gcore requires provider_catalogs.gcore_project_id and gcore_region_ids")
    if require_credentials and "saladcloud" in config.provider_catalog_sources and (not config.salad_organization or config.salad_organization.startswith("REPLACE_")):
        raise ConfigError("SaladCloud requires provider_catalogs.salad_organization")


def verify_provider_config(config: Config, source: str) -> None:
    """Validate only the account data needed by one independent provider job."""
    requirements = {"runpod":"RUNPOD_API_KEY", "lambda":"LAMBDA_API_KEY", "clore":"CLORE_API_KEY", "saladcloud":"SALAD_API_KEY", "google_cloud":"GOOGLE_CLOUD_API_KEY", "gcore":"GCORE_API_KEY"}
    name = requirements.get(source)
    if name and not os.environ.get(name):
        raise ConfigError(f"{name} is required when {source} is enabled")
    if source == "gcore" and (not config.gcore_project_id or not config.gcore_region_ids or config.gcore_project_id.startswith("REPLACE_") or any(region.startswith("REPLACE_") for region in config.gcore_region_ids)):
        raise ConfigError("Gcore requires real provider_catalogs.gcore_project_id and gcore_region_ids")
    if source == "saladcloud" and (not config.salad_organization or config.salad_organization.startswith("REPLACE_")):
        raise ConfigError("SaladCloud requires a real provider_catalogs.salad_organization")
