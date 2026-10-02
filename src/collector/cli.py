import json
import os
from pathlib import Path
import sqlite3

import typer
from . import launchd
from .collect import collect as run_collection, initialize
from .config import load, load_environment
from .locking import AlreadyRunning

app = typer.Typer(no_args_is_help=True)
schedule = typer.Typer(no_args_is_help=True)
app.add_typer(schedule, name="launchd")


@app.callback()
def configure(ctx: typer.Context, config: Path = Path("config.toml")):
    os.umask(0o077)
    ctx.obj = load(config)


@app.command()
def init(ctx: typer.Context):
    """Create the database and apply migrations. Never install a schedule."""
    initialize(ctx.obj)
    typer.echo(f"Initialized {ctx.obj.data_dir}")


@app.command()
def collect(ctx: typer.Context):
    """Collect one complete snapshot."""
    load_environment(ctx.obj.root)
    try:
        typer.echo(run_collection(ctx.obj))
    except AlreadyRunning:
        typer.echo("Skipped: collection is already running")


@app.command()
def replay(ctx: typer.Context, retrieval_id: str):
    """Reparse stored bytes without source access or checkpoint changes."""
    typer.echo(run_collection(ctx.obj, retrieval_id))


@app.command()
def status(ctx: typer.Context, json_output: bool = typer.Option(False, "--json")):
    path = ctx.obj.data_dir / "collector.sqlite3"
    result = {"runs": [], "checkpoint": None}
    if path.exists():
        db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        try:
            result["runs"] = [dict(row) for row in db.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT 20")]
            row = db.execute("SELECT measured_at FROM checkpoint WHERE id=1").fetchone()
            result["checkpoint"] = row[0] if row else None
        finally:
            db.close()
    typer.echo(json.dumps(result, indent=None if json_output else 2))


@schedule.command("install")
def install(ctx: typer.Context):
    typer.echo(str(launchd.install(ctx.obj)))


@schedule.command("uninstall")
def uninstall(ctx: typer.Context):
    launchd.uninstall(ctx.obj)
    typer.echo("Uninstalled schedule. Stored data remains unchanged.")


@schedule.command("status")
def launchd_status(ctx: typer.Context):
    typer.echo(launchd.status(ctx.obj))


def main():
    try:
        app()
    except Exception as error:
        # HTTP errors can embed signed URLs and credentials. Do not print them.
        typer.echo(f"Command failed ({type(error).__name__}). See README.md for recovery.", err=True)
        raise SystemExit(1) from None
