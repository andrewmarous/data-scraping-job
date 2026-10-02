"""Emit only allowlisted fields. Never log upstream exception messages."""
from datetime import datetime, timezone
import json
from pathlib import Path


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def event(data_dir: Path, name: str, run_id: str, *, error_class=None, record_count=None):
    directory = data_dir / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    value = dict(time=now(), event=name, run_id=run_id)
    if error_class:
        value["error_class"] = error_class
    if record_count is not None:
        value["record_count"] = record_count
    with (directory / "collector.jsonl").open("a") as stream:
        stream.write(json.dumps(value) + "\n")
