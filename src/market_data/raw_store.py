from __future__ import annotations

import gzip
import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class RawObject:
    path: Path
    sha256: str
    size: int


def store_raw(data_dir: Path, source: str, retrieved_at: str, run_id: str, body: bytes) -> RawObject:
    moment = datetime.fromisoformat(retrieved_at.replace("Z", "+00:00")).astimezone(timezone.utc)
    digest = hashlib.sha256(body).hexdigest()
    directory = data_dir / "raw" / source / moment.strftime("%Y/%m/%d")
    directory.mkdir(parents=True, exist_ok=True)
    stamp = moment.strftime("%Y%m%dT%H%M%S.%fZ")
    target = directory / f"{stamp}-{run_id}-{digest[:12]}.json.gz"
    descriptor, temporary = tempfile.mkstemp(prefix=".raw-", dir=directory)
    try:
        with os.fdopen(descriptor, "wb") as raw_stream:
            with gzip.GzipFile(fileobj=raw_stream, mode="wb", mtime=0) as compressed:
                compressed.write(body)
            raw_stream.flush()
            os.fsync(raw_stream.fileno())
        os.replace(temporary, target)
        directory_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    return RawObject(target, digest, len(body))


def quarantine(data_dir: Path, source: str, raw_path: Path) -> Path:
    destination = data_dir / "quarantine" / source
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / raw_path.name
    try:
        os.link(raw_path, target)
    except OSError:
        shutil.copy2(raw_path, target)
    return target
