from __future__ import annotations

import csv
import io
import json
import logging
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import typer
import httpx

from . import __version__
from .collectors.openrouter import PARSER_VERSION, collect_partition, completed_utc_day, daily_window, plan_ranges
from .collectors.openrouter_batch import PARSER_VERSION as BATCH_PARSER_VERSION, collect as collect_batch
from .collectors.vast import PARSER_VERSION as VAST_PARSER_VERSION, collect_contract
from .collectors.aws_spot import PARSER_VERSION as AWS_PARSER_VERSION, collect_all
from .collectors.provider_catalog import PARSER_VERSION as PROVIDER_PARSER_VERSION, SOURCES as PROVIDER_SOURCES, collect_source
from .config import ConfigError, load_config, verify_config, verify_provider_config
from .database import connect, initialize_dirs, migrate, utc_now
from .errors import LockActive, MarketDataError, SchemaError
from .locking import SourceLock
from .index_verification import CANDIDATES
from .logging import configure_logging
from .scheduler import install as install_launchd, uninstall as uninstall_launchd
from .secrets import load_local_environment

app = typer.Typer(no_args_is_help=True)
collect_app = typer.Typer(no_args_is_help=True)
backfill_app = typer.Typer(no_args_is_help=True)
launchd_app = typer.Typer(no_args_is_help=True)
indexes_app = typer.Typer(no_args_is_help=True)
report_app = typer.Typer(no_args_is_help=True)
app.add_typer(collect_app, name="collect")
app.add_typer(backfill_app, name="backfill")
app.add_typer(launchd_app, name="launchd")
app.add_typer(indexes_app, name="indexes")
app.add_typer(report_app, name="report")
PROJECT_DIR = Path(__file__).parents[2]
MIGRATIONS = PROJECT_DIR / "migrations"


def _config():
    load_local_environment(PROJECT_DIR)
    return load_config()


def _prepare(config):
    initialize_dirs(config.data_dir)
    migrate(config.database_path, MIGRATIONS)
    configure_logging(config.data_dir, config.log_level, config.log_retention_days)
    connection=connect(config.database_path)
    schedules=[('openrouter','daily',1440),('openrouter_batch','daily',1440),('aws_spot','all-regions',1440)]
    schedules += [('vast',contract,config.vast_bid_interval_minutes if contract=='interruptible' else config.vast_other_interval_minutes) for contract in config.vast_contracts]
    schedules += [(source,'catalog',config.provider_catalog_interval_minutes) for source in config.provider_catalog_sources]
    for source,partition,minutes in schedules:
        connection.execute("INSERT INTO source_schedule_expectations VALUES(?,?,?,?) ON CONFLICT(source,partition_key) DO UPDATE SET interval_minutes=excluded.interval_minutes,updated_at_utc=excluded.updated_at_utc",(source,partition,minutes,utc_now()))
    connection.commit(); connection.close()


def _run_partition(config, partition, command: str) -> None:
    run_id = str(uuid.uuid4())
    connection = connect(config.database_path)
    connection.execute("INSERT INTO collection_runs(run_id,source,partition_key,command,collector_version,parser_version,started_at_utc,status) VALUES(?,?,?,?,?,?,?,?)",
                       (run_id, "openrouter", partition.key, command, __version__, PARSER_VERSION, utc_now(), "running"))
    connection.commit()
    connection.close()
    logger=logging.getLogger("market_data")
    logger.info("Collection started",extra={"run_id":run_id,"source":"openrouter","partition_key":partition.key,"event":"run_started"})
    try:
        parsed, inserted, revised = collect_partition(config, run_id, partition)
    except Exception as exc:
        connection = connect(config.database_path)
        state = "quarantined" if isinstance(exc, SchemaError) else "failed"
        connection.execute("UPDATE collection_runs SET finished_at_utc=?,status=?,error_class=?,error_message=? WHERE run_id=?",
                           (utc_now(), state, type(exc).__name__, str(exc)[:1000], run_id))
        connection.commit(); connection.close()
        logger.error(str(exc),extra={"run_id":run_id,"source":"openrouter","partition_key":partition.key,"event":"run_failed","error_class":type(exc).__name__})
        raise
    connection = connect(config.database_path)
    connection.execute("UPDATE collection_runs SET finished_at_utc=?,status='succeeded',parsed_count=?,inserted_count=?,revised_count=? WHERE run_id=?",
                       (utc_now(), parsed, inserted, revised, run_id))
    connection.commit(); connection.close()
    logger.info("Collection succeeded",extra={"run_id":run_id,"source":"openrouter","partition_key":partition.key,"event":"run_succeeded","record_count":inserted})


def _run_source(config, source: str, partition_key: str, command: str, parser_version: str, function) -> None:
    run_id = str(uuid.uuid4())
    connection = connect(config.database_path)
    connection.execute("INSERT INTO collection_runs(run_id,source,partition_key,command,collector_version,parser_version,started_at_utc,status) VALUES(?,?,?,?,?,?,?,?)",
                       (run_id, source, partition_key, command, __version__, parser_version, utc_now(), "running"))
    connection.commit(); connection.close()
    logger=logging.getLogger("market_data")
    logger.info("Collection started",extra={"run_id":run_id,"source":source,"partition_key":partition_key,"event":"run_started"})
    try:
        parsed, inserted, revised = function(run_id)
    except Exception as exc:
        connection = connect(config.database_path)
        connection.execute("UPDATE collection_runs SET finished_at_utc=?,status=?,error_class=?,error_message=? WHERE run_id=?",
                           (utc_now(), "quarantined" if isinstance(exc, SchemaError) else "failed", type(exc).__name__, str(exc)[:1000], run_id))
        connection.commit(); connection.close()
        logger.error(str(exc),extra={"run_id":run_id,"source":source,"partition_key":partition_key,"event":"run_failed","error_class":type(exc).__name__})
        raise
    connection = connect(config.database_path)
    connection.execute("UPDATE collection_runs SET finished_at_utc=?,status='succeeded',parsed_count=?,inserted_count=?,revised_count=? WHERE run_id=?",
                       (utc_now(), parsed, inserted, revised, run_id))
    connection.commit(); connection.close()
    logger.info("Collection succeeded",extra={"run_id":run_id,"source":source,"partition_key":partition_key,"event":"run_succeeded","record_count":inserted})


def _record_skipped(config,source,partition_key,command,parser_version):
    run_id=str(uuid.uuid4()); timestamp=utc_now(); connection=connect(config.database_path)
    connection.execute("INSERT INTO collection_runs(run_id,source,partition_key,command,collector_version,parser_version,started_at_utc,finished_at_utc,status) VALUES(?,?,?,?,?,?,?,?,?)",(run_id,source,partition_key,command,__version__,parser_version,timestamp,timestamp,"skipped_already_running"))
    connection.commit(); connection.close()
    logging.getLogger("market_data").info("Collection skipped because the lock is active",extra={"run_id":run_id,"source":source,"partition_key":partition_key,"event":"run_skipped"})
    typer.echo(f"Skipped: {source} partition {partition_key} is already running")


def _collect_ranges(config, ranges, command):
    if not config.openrouter_enabled:
        raise ConfigError("OpenRouter is disabled")
    verify_config(config)
    _prepare(config)
    try:
        with SourceLock(config.data_dir / "locks" / "openrouter.lock", config.max_run_minutes * 60):
            for partition in ranges: _run_partition(config, partition, command)
    except LockActive:
        _record_skipped(config,"openrouter",ranges[0].key if ranges else "none",command,PARSER_VERSION)


@app.command("init")
def init_command():
    """Create runtime directories and apply migrations."""
    config = _config(); _prepare(config)
    typer.echo(f"Initialized {config.data_dir}")


@app.command("migrate")
def migrate_command():
    """Apply pending database migrations."""
    config = _config(); initialize_dirs(config.data_dir)
    versions = migrate(config.database_path, MIGRATIONS)
    typer.echo("Applied: " + (", ".join(map(str, versions)) if versions else "none"))


@app.command("verify-config")
def verify_config_command():
    """Validate local configuration and credentials."""
    config = _config(); verify_config(config)
    typer.echo("Configuration is valid")


def _date_option(value: Optional[str], name: str) -> Optional[date]:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must use YYYY-MM-DD") from exc


@collect_app.command("openrouter")
def collect_openrouter(start: Optional[str] = typer.Option(None), end: Optional[str] = typer.Option(None)):
    config = _config(); final = _date_option(end, "end") or completed_utc_day()
    first = _date_option(start, "start") or (final - timedelta(days=config.openrouter_overlap_days))
    _collect_ranges(config, plan_ranges(first, final), "collect openrouter")


def _collect_batch(config, day, retry=False, command='collect openrouter-batch'):
    if not config.openrouter_batch_enabled: raise ConfigError('OpenRouter batch is disabled')
    if day > completed_utc_day(): raise ConfigError('Batch collection date must be a completed UTC day')
    if not os.environ.get('OPENROUTER_API_KEY'): raise ConfigError('OPENROUTER_API_KEY is required')
    _prepare(config)
    try:
        with SourceLock(config.data_dir / 'locks' / 'openrouter-batch.lock', config.max_run_minutes * 60):
            db = connect(config.database_path)
            done = db.execute("SELECT 1 FROM collection_runs WHERE source='openrouter_batch' AND partition_key=? AND status='succeeded' LIMIT 1", (day.isoformat(),)).fetchone()
            db.close()
            if done and not retry: return
            _run_source(config, 'openrouter_batch', day.isoformat(), command, BATCH_PARSER_VERSION,
                        lambda run_id: collect_batch(config, run_id, day))
    except LockActive:
        _record_skipped(config, 'openrouter_batch', day.isoformat(), command, BATCH_PARSER_VERSION)


@collect_app.command('openrouter-batch')
def collect_openrouter_batch(day: Optional[str] = typer.Option(None), retry: bool = typer.Option(False)):
    config = _config()
    _collect_batch(config, _date_option(day, 'day') or completed_utc_day(), retry)


@collect_app.command("vast")
def collect_vast(contract: Optional[str] = typer.Option(None)):
    config = _config()
    if not config.vast_enabled: raise ConfigError("Vast is disabled")
    verify_config(config); _prepare(config)
    contracts = (contract,) if contract else config.vast_contracts
    for selected in contracts:
        try:
            with SourceLock(config.data_dir / "locks" / f"vast-{selected}.lock", config.max_run_minutes * 60):
                _run_source(config,"vast",selected,"collect vast",VAST_PARSER_VERSION,
                            lambda run_id, selected=selected: collect_contract(config,run_id,selected))
        except LockActive: _record_skipped(config,"vast",selected,"collect vast",VAST_PARSER_VERSION)


@collect_app.command("aws-spot")
def collect_aws_spot(region: Optional[str] = typer.Option(None)):
    config = _config()
    if not config.aws_spot_enabled: raise ConfigError("AWS Spot is disabled")
    verify_config(config); _prepare(config)
    key = region or "all-regions"
    try:
        with SourceLock(config.data_dir / "locks" / "aws-spot.lock", config.max_run_minutes * 60):
            _run_source(config,"aws_spot",key,"collect aws-spot",AWS_PARSER_VERSION,
                        lambda run_id: collect_all(config,run_id,region))
    except LockActive: _record_skipped(config,"aws_spot",key,"collect aws-spot",AWS_PARSER_VERSION)


@collect_app.command("provider")
def collect_provider(source: str):
    """Collect one configured provider's read-only market catalog."""
    config = _config()
    if source not in PROVIDER_SOURCES: raise ConfigError(f"Unknown provider source: {source}")
    if source not in config.provider_catalog_sources: raise ConfigError(f"Provider source is disabled: {source}")
    verify_provider_config(config, source); _prepare(config)
    try:
        with SourceLock(config.data_dir / "locks" / f"{source}.lock", config.max_run_minutes * 60):
            _run_source(config,source,"catalog",f"collect provider {source}",PROVIDER_PARSER_VERSION,lambda run_id: collect_source(config,run_id,source))
    except LockActive: _record_skipped(config,source,"catalog",f"collect provider {source}",PROVIDER_PARSER_VERSION)


@collect_app.command("due")
def collect_due():
    config = _config(); _prepare(config)
    def is_due(source, partition, interval):
        connection=connect(config.database_path)
        row=connection.execute("SELECT finished_at_utc FROM collection_runs WHERE source=? AND (? IS NULL OR partition_key=?) AND status='succeeded' ORDER BY finished_at_utc DESC LIMIT 1",(source,partition,partition)).fetchone()
        connection.close()
        return not row or datetime.fromisoformat(row[0].replace('Z','+00:00')) < datetime.now(timezone.utc)-interval
    if config.vast_enabled:
        verify_config(config)
        for selected in config.vast_contracts:
            minutes=config.vast_bid_interval_minutes if selected=='interruptible' else config.vast_other_interval_minutes
            if not is_due('vast',selected,timedelta(minutes=minutes)): continue
            try:
                with SourceLock(config.data_dir / "locks" / f"vast-{selected}.lock", config.max_run_minutes * 60):
                    _run_source(config,"vast",selected,"collect due",VAST_PARSER_VERSION,
                                lambda run_id, selected=selected: collect_contract(config,run_id,selected))
            except LockActive: _record_skipped(config,"vast",selected,"collect due",VAST_PARSER_VERSION)
    if config.aws_spot_enabled:
        verify_config(config)
        if is_due('aws_spot',None,timedelta(days=1)):
            try:
                with SourceLock(config.data_dir / "locks" / "aws-spot.lock", config.max_run_minutes * 60):
                    _run_source(config,"aws_spot","all-regions","collect due",AWS_PARSER_VERSION,
                                lambda run_id: collect_all(config,run_id))
            except LockActive: _record_skipped(config,"aws_spot","all-regions","collect due",AWS_PARSER_VERSION)
    for source in config.provider_catalog_sources:
        if not is_due(source,"catalog",timedelta(minutes=config.provider_catalog_interval_minutes)): continue
        try:
            with SourceLock(config.data_dir / "locks" / f"{source}.lock", config.max_run_minutes * 60):
                _run_source(config,source,"catalog","collect due",PROVIDER_PARSER_VERSION,lambda run_id,source=source: collect_source(config,run_id,source))
        except LockActive: _record_skipped(config,source,"catalog","collect due",PROVIDER_PARSER_VERSION)
    if config.openrouter_batch_enabled:
        _collect_batch(config, completed_utc_day(), command='collect due')
    if not config.openrouter_enabled:
        return
    connection = connect(config.database_path)
    row = connection.execute("SELECT finished_at_utc FROM collection_runs WHERE source='openrouter' AND status='succeeded' ORDER BY finished_at_utc DESC LIMIT 1").fetchone()
    connection.close()
    due = not row or datetime.fromisoformat(row[0].replace("Z", "+00:00")) < datetime.now(timezone.utc) - timedelta(days=1)
    if due:
        final = completed_utc_day()
        _collect_ranges(config, [daily_window(final, config.openrouter_overlap_days)], "collect due")


@backfill_app.command("openrouter")
def backfill_openrouter(start: str = typer.Option(...), end: Optional[str] = typer.Option(None)):
    config = _config(); final = _date_option(end, "end") or completed_utc_day()
    first = _date_option(start, "start")
    assert first is not None
    _collect_ranges(config, plan_ranges(first, final), "backfill openrouter")


@app.command("status")
def status(json_output: bool = typer.Option(False, "--json")):
    config = _config(); _prepare(config)
    connection = connect(config.database_path)
    result=[]
    specs=[("openrouter","OpenRouter",config.openrouter_enabled,"openrouter_daily_tokens","observation_date","is_current=1",timedelta(hours=36)),
           ("vast","Vast",config.vast_enabled,"vast_offer_snapshots","collected_at_utc","1=1",timedelta(minutes=max(config.vast_bid_interval_minutes,config.vast_other_interval_minutes)*1.5)),
           ("aws_spot","AWS Spot",config.aws_spot_enabled,"aws_spot_prices","source_timestamp_utc","1=1",timedelta(hours=36))]
    specs += [(source,source.replace('_',' ').title(),True,"provider_market_observations","collected_at_utc",f"provider='{source}'",timedelta(minutes=config.provider_catalog_interval_minutes*1.5)) for source in config.provider_catalog_sources]
    for source,label,enabled,table,column,where,max_age in specs:
        runs=connection.execute("SELECT * FROM collection_runs WHERE source=? ORDER BY started_at_utc DESC",(source,)).fetchall()
        latest=runs[0] if runs else None; success=next((r for r in runs if r['status']=='succeeded'),None); failures=0
        for run in runs:
            if run['status']=='succeeded': break
            if run['status'] in {'failed','quarantined'}: failures+=1
        records=connection.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()[0]
        newest=connection.execute(f"SELECT MAX({column}) FROM {table} WHERE {where}").fetchone()[0]
        if not enabled: freshness='disabled'
        elif not success: freshness='never'
        elif latest and latest['status'] in {'failed','quarantined'} and failures>=3: freshness='failing'
        else: freshness='healthy' if datetime.now(timezone.utc)-datetime.fromisoformat(success['finished_at_utc'].replace('Z','+00:00'))<=max_age else 'stale'
        result.append({'source':label,'last_attempt':latest['started_at_utc'] if latest else None,'last_success':success['finished_at_utc'] if success else None,'freshness':freshness,'records':records,'consecutive_failures':failures,'newest_observation':newest})
    connection.close()
    if json_output: typer.echo(json.dumps(result, separators=(",", ":")))
    else:
        typer.echo("Source       Last attempt          Last success          Freshness   Records   Failures")
        for row in result: typer.echo(f"{row['source']:12} {row['last_attempt'] or '-':20}  {row['last_success'] or '-':20}  {row['freshness']:10}  {row['records']:7}   {row['consecutive_failures']}")


@report_app.command("query")
def report_query(name: str, format: str = typer.Option("jsonl"), output: Optional[Path] = typer.Option(None)):
    """Export an approved analytical view as JSONL or CSV."""
    allowed={"openrouter_daily_total","openrouter_model_share","openrouter_token_volume_quality","openrouter_model_day_change","vast_offer_depth","vast_listing_history","vast_price_observations","vast_minimum_bid_movement","aws_spot_per_gpu","aws_region_coverage","source_run_summary","source_poll_completeness","source_data_quality","source_version_report","unmapped_source_values","cross_cloud_price_comparison","provider_market_latest"}
    if name not in allowed or format not in {"jsonl","csv"}: raise ConfigError("Unknown report or format")
    config=_config(); _prepare(config); connection=connect(config.database_path)
    rows=[dict(row) for row in connection.execute(f"SELECT * FROM {name}")]
    versions=[row[0] for row in connection.execute("SELECT version_id FROM mapping_versions ORDER BY created_at_utc,version_id")]
    connection.close(); stream=io.StringIO(); generated=utc_now(); version_text=",".join(versions)
    if format=='jsonl':
        stream.write(json.dumps({'_metadata':{'report':name,'generated_at_utc':generated,'mapping_versions':versions,'gpu_catalog':'effective-time-versioned'}},separators=(',',':'))+'\n')
        for row in rows: stream.write(json.dumps(row,separators=(',',':'))+'\n')
    else:
        exported=[{'generated_at_utc':generated,'mapping_versions':version_text,**row} for row in rows]
        fields=list(exported[0]) if exported else ['generated_at_utc','mapping_versions']
        writer=csv.DictWriter(stream,fieldnames=fields); writer.writeheader(); writer.writerows(exported)
    if output: output.write_text(stream.getvalue())
    else: typer.echo(stream.getvalue(),nl=False)


@indexes_app.command("status")
def index_status(json_output: bool = typer.Option(False, "--json")):
    """Show Release 4 source-verification decisions."""
    results = [candidate.as_dict() for candidate in CANDIDATES.values()]
    if json_output:
        typer.echo(json.dumps(results, separators=(",", ":")))
        return
    typer.echo("Source          Status                  Eligible  Reason")
    for result in results:
        typer.echo(
            f"{result['source']:15} {result['status']:23} "
            f"{str(result['eligible']).lower():8}  {result['reason']}"
        )


@launchd_app.command("install")
def launchd_install():
    config = _config(); _prepare(config)
    verify_config(config, require_credentials=False)
    for path in install_launchd(PROJECT_DIR, config.data_dir, config): typer.echo(path)


@launchd_app.command("uninstall")
def launchd_uninstall():
    for path in uninstall_launchd(): typer.echo(path)


def main():
    try:
        app()
    except LockActive:
        typer.echo("Skipped: this collection partition is already running")
    except (MarketDataError, ConfigError, httpx.HTTPError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise SystemExit(1)
