from types import SimpleNamespace

from market_data.collectors.aws_spot import parse_catalog
from market_data.database import connect, migrate
from market_data.scheduler import _jobs
from market_data.cli import MIGRATIONS
from market_data.cli import _record_skipped


def test_all_enabled_sources_have_independent_launchd_jobs():
    config=SimpleNamespace(openrouter_enabled=True,vast_enabled=True,aws_spot_enabled=True,
        vast_contracts=("on-demand","interruptible","reserved"),vast_bid_interval_minutes=5,vast_other_interval_minutes=10)
    jobs=_jobs(config)
    labels={job[0] for job in jobs}
    assert labels=={"local.market-data.openrouter","local.market-data.vast.interruptible","local.market-data.vast.on-demand","local.market-data.vast.reserved","local.market-data.aws-spot"}
    assert next(values["__INTERVAL__"] for label,_,values in jobs if label.endswith("interruptible"))=="300"


def test_provider_sources_have_independent_launchd_jobs():
    config=SimpleNamespace(openrouter_enabled=False,vast_enabled=False,aws_spot_enabled=False,
        provider_catalog_sources=("runpod","azure","google_cloud"),provider_catalog_interval_minutes=60)
    jobs=_jobs(config)
    assert [job[0] for job in jobs] == ["local.market-data.runpod","local.market-data.azure","local.market-data.google-cloud"]


def test_aws_catalog_parser_and_reporting_views(tmp_path):
    body=b'{"InstanceTypes":[{"InstanceType":"g5.xlarge","GpuInfo":{"Gpus":[{"Name":"A10G","Count":1,"MemoryInfo":{"SizeInMiB":22888}}]},"VCpuInfo":{"DefaultVCpus":4},"MemoryInfo":{"SizeInMiB":16384}}]}'
    row=parse_catalog(body)[0]
    assert (row["accelerator_model"],row["gpu_count"],row["gpu_memory_mb"])==("A10G",1,22888)
    database=tmp_path/"report.sqlite3"; migrate(database,MIGRATIONS); connection=connect(database)
    views={r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='view'")}
    assert {"source_run_summary","vast_offer_depth","vast_listing_history","aws_spot_per_gpu","aws_region_coverage","openrouter_model_share","source_poll_completeness","source_data_quality","source_version_report","unmapped_source_values","cross_cloud_price_comparison"} <= views


def test_active_collection_skip_is_recorded(tmp_path):
    database=tmp_path/"skip.sqlite3"; migrate(database,MIGRATIONS)
    config=SimpleNamespace(database_path=database)
    _record_skipped(config,"vast","interruptible","collect vast","1")
    row=connect(database).execute("SELECT status,partition_key FROM collection_runs").fetchone()
    assert tuple(row)==("skipped_already_running","interruptible")
