from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_FIELDS = ("run_id", "source", "partition_key", "event", "attempt_number", "record_count", "duration_ms", "error_class")
_SECRET_NAMES = ("OPENROUTER_API_KEY", "VAST_API_KEY", "RUNPOD_API_KEY", "GCORE_API_KEY", "CLORE_API_KEY", "SALAD_API_KEY", "GOOGLE_CLOUD_API_KEY")
_AUTHORIZATION = re.compile(r"(?i)(authorization|api[-_ ]?key|bearer)(\s*[:=]?\s*)([^\s,;]+)")


def redact(value: Any) -> Any:
    """Remove recognized credentials and authorization-like values."""
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if any(token in str(key).lower() for token in ("authorization", "api_key", "apikey", "token", "secret")) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if not isinstance(value, str):
        return value
    result = value
    for name in _SECRET_NAMES:
        secret = os.environ.get(name)
        if secret:
            result = result.replace(secret, "[REDACTED]")
    return _AUTHORIZATION.sub(lambda match: match.group(1) + match.group(2) + "[REDACTED]", result)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        item: dict[str, Any] = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname,
            "message": redact(record.getMessage()),
        }
        for field in _FIELDS:
            item[field] = redact(getattr(record, field, None))
        return json.dumps(redact(item), separators=(",", ":"), ensure_ascii=False)


def configure_logging(data_dir: Path, level: str, retention_days: int = 30) -> logging.Logger:
    directory = data_dir / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "market-data.jsonl"
    if path.exists() and path.stat().st_size >= 10 * 1024 * 1024:
        stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        rotated = directory / f"market-data.jsonl.{stamp}"
        os.replace(path, rotated)
    cutoff=time.time()-retention_days*86400
    for old in directory.glob("market-data.jsonl.*"):
        if old.stat().st_mtime < cutoff: old.unlink()
    logger = logging.getLogger("market_data")
    logger.handlers.clear()
    logger.setLevel(level)
    handler = logging.FileHandler(path)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    return logger
