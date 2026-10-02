from __future__ import annotations

import json
import os
import socket
import time
from pathlib import Path

from .errors import LockActive


class SourceLock:
    def __init__(self, path: Path, max_age_seconds: int):
        self.path = path
        self.max_age_seconds = max_age_seconds
        self.acquired = False

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"pid": os.getpid(), "hostname": socket.gethostname(), "started": time.time()}
        while True:
            try:
                descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "w") as stream:
                    json.dump(payload, stream)
                self.acquired = True
                return self
            except FileExistsError:
                try:
                    existing = json.loads(self.path.read_text())
                    age = time.time() - float(existing["started"])
                    alive = existing.get("hostname") == socket.gethostname() and _pid_alive(int(existing["pid"]))
                    if alive and age <= self.max_age_seconds:
                        raise LockActive("collection is already running")
                    self.path.unlink()
                except LockActive:
                    raise
                except (OSError, ValueError, KeyError, json.JSONDecodeError):
                    try:
                        self.path.unlink()
                    except FileNotFoundError:
                        pass

    def __exit__(self, *_):
        if self.acquired:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
