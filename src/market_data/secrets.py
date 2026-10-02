from __future__ import annotations

import os
from pathlib import Path

from .config import load_env_file


def load_local_environment(project_dir: Path) -> None:
    path = project_dir / ".env"
    if not path.exists():
        return
    for name, value in load_env_file(path).items():
        os.environ.setdefault(name, value)
