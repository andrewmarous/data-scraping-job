from __future__ import annotations

import os
import plistlib
import shutil
import stat
import subprocess
from pathlib import Path

from .errors import MarketDataError

PREFIX = "local.market-data"


def _jobs(config):
    jobs = []
    if config.openrouter_enabled:
        jobs.append((f"{PREFIX}.openrouter", "openrouter.plist.template", {}))
    if getattr(config, 'openrouter_batch_enabled', False):
        jobs.append((f"{PREFIX}.openrouter-batch", "openrouter-batch.plist.template", {}))
    if config.vast_enabled:
        if "interruptible" in config.vast_contracts:
            jobs.append((f"{PREFIX}.vast.interruptible", "vast-interruptible.plist.template", {"__INTERVAL__": str(config.vast_bid_interval_minutes * 60)}))
        for contract in ("on-demand", "reserved"):
            if contract in config.vast_contracts:
                jobs.append((f"{PREFIX}.vast.{contract}", "vast-other.plist.template", {"__INTERVAL__": str(config.vast_other_interval_minutes * 60), "__CONTRACT__": contract}))
    if config.aws_spot_enabled:
        jobs.append((f"{PREFIX}.aws-spot", "aws-spot.plist.template", {}))
    for source in getattr(config, "provider_catalog_sources", ()):
        jobs.append((f"{PREFIX}.{source.replace('_','-')}", "provider-catalog.plist.template", {"__SOURCE__":source, "__INTERVAL__":str(config.provider_catalog_interval_minutes*60)}))
    return jobs


def install(project_dir: Path, data_dir: Path, config) -> list[Path]:
    if __import__("sys").platform != "darwin":
        raise MarketDataError("launchd installation requires macOS")
    env_file = project_dir / ".env"
    if not env_file.exists() or env_file.is_symlink() or env_file.stat().st_uid != os.getuid() or stat.S_IMODE(env_file.stat().st_mode) & 0o077:
        raise MarketDataError(f"Create {env_file} with mode 0600 before installation")
    logs = data_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    wrapper = data_dir / "market-data-launchd"
    uv = shutil.which("uv")
    if not uv:
        raise MarketDataError("uv is required for launchd installation")
    aws = shutil.which("aws")
    path = "/usr/bin:/bin:/usr/sbin:/sbin" + (f":{Path(aws).parent}" if aws else "")
    wrapper.write_text(
        f"#!/bin/sh\nexport PATH='{path}'\nexport MARKET_DATA_CONFIG='{config.path}'\n"
        "unset OPENROUTER_API_KEY VAST_API_KEY AWS_PROFILE RUNPOD_API_KEY GCORE_API_KEY CLORE_API_KEY SALAD_API_KEY GOOGLE_CLOUD_API_KEY\n"
        f"exec '{uv}' run --project '{project_dir}' --env-file '{env_file}' market-data \"$@\"\n"
    )
    wrapper.chmod(0o700)
    agents = Path.home() / "Library" / "LaunchAgents"
    agents.mkdir(parents=True, exist_ok=True)
    installed = []
    for label, template_name, replacements in _jobs(config):
        content = (project_dir / "launchd" / template_name).read_text()
        values = {"__LABEL__": label, "__WRAPPER__": str(wrapper), "__LOG_DIR__": str(logs), **replacements}
        for old, new in values.items(): content = content.replace(old, new)
        destination = agents / f"{label}.plist"
        destination.write_text(content)
        try:
            plistlib.loads(destination.read_bytes())
            subprocess.run(["plutil", "-lint", str(destination)], check=True, capture_output=True)
            subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(destination)], check=False, capture_output=True)
            subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(destination)], check=True, capture_output=True)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        installed.append(destination)
    return installed


def uninstall() -> list[Path]:
    removed = []
    for destination in sorted((Path.home() / "Library" / "LaunchAgents").glob(f"{PREFIX}*.plist")):
        if __import__("sys").platform == "darwin":
            subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(destination)], check=False)
        destination.unlink()
        removed.append(destination)
    return removed
