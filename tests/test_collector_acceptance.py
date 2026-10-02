import json
import subprocess
import uuid
from datetime import date
from pathlib import Path

import httpx
import pytest

from market_data.collectors.aws_spot import collect_all, discover_regions
from market_data.collectors.base import Partition
from market_data.collectors.openrouter import collect_partition
from market_data.collectors.vast import collect_contract
from market_data.config import load_config
from market_data.database import connect, initialize_dirs, migrate, utc_now
from market_data.errors import SchemaError

ROOT=Path(__file__).parents[1]


def configured(tmp_path):
    path=tmp_path/"config.toml"
    path.write_text(f'''[data]\ndirectory="{tmp_path / 'var'}"\n[openrouter]\nenabled=true\n[vast]\nenabled=true\n[aws_spot]\nenabled=true\nregions=["us-east-1"]\ninstance_families=["g5"]\naws_profile="test"\n''')
    config=load_config(path); initialize_dirs(config.data_dir); migrate(config.database_path,ROOT/"migrations")
    return config


def run_row(config,source,parser="1"):
    run_id=str(uuid.uuid4()); connection=connect(config.database_path)
    connection.execute("INSERT INTO collection_runs(run_id,source,partition_key,command,collector_version,parser_version,started_at_utc,status) VALUES(?,?,?,?,?,?,?,?)",(run_id,source,"test","test","test",parser,utc_now(),"running"))
    connection.commit(); connection.close(); return run_id


def test_openrouter_retries_versions_and_quarantines_atomically(tmp_path):
    config=configured(tmp_path); fixture=(ROOT/"tests/fixtures/openrouter.json").read_bytes(); calls=0
    def handler(request):
        nonlocal calls; calls+=1
        return httpx.Response(429,request=request) if calls==1 else httpx.Response(200,content=fixture,request=request)
    run=run_row(config,"openrouter")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert collect_partition(config,run,Partition(date(2025,1,1),date(2025,1,1)),client=client,sleep=lambda _:None)[1:]==(2,0)
    connection=connect(config.database_path)
    assert connection.execute("SELECT COUNT(*) FROM raw_retrievals WHERE run_id=?",(run,)).fetchone()[0]==2
    changed=json.loads(fixture); changed["data"][0]["total_tokens"]="999"; changed_body=json.dumps(changed).encode()
    run2=run_row(config,"openrouter")
    with httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(200,content=changed_body,request=request))) as client:
        assert collect_partition(config,run2,Partition(date(2025,1,1),date(2025,1,1)),client=client,sleep=lambda _:None)[1:]==(1,1)
    assert connection.execute("SELECT COUNT(*) FROM openrouter_daily_tokens WHERE supersedes_observation_id IS NOT NULL").fetchone()[0]==1
    run3=run_row(config,"openrouter")
    with httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(200,content=b'{}',request=request))) as client:
        with pytest.raises(SchemaError): collect_partition(config,run3,Partition(date(2025,1,1),date(2025,1,1)),client=client,sleep=lambda _:None)
    assert list((config.data_dir/"quarantine/openrouter").glob("*.json.gz"))


def test_vast_retries_rate_limit_without_sleeping(tmp_path):
    config=configured(tmp_path); fixture=(ROOT/"tests/fixtures/vast/bid.json").read_bytes(); calls=0
    def handler(request):
        nonlocal calls; calls+=1
        return httpx.Response(429,request=request) if calls==1 else httpx.Response(200,content=fixture,request=request)
    run=run_row(config,"vast")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        parsed,inserted,_=collect_contract(config,run,"interruptible",client=client,sleep=lambda _:None)
    assert parsed==inserted==1
    connection=connect(config.database_path)
    assert connection.execute("SELECT COUNT(*) FROM raw_retrievals WHERE run_id=?",(run,)).fetchone()[0]==2


def test_aws_pagination_catalog_archival_and_throttle_retry(tmp_path,monkeypatch):
    config=configured(tmp_path); throttled=True; offering_throttled=True; gpu_count=1
    def runner(command,**kwargs):
        nonlocal throttled,offering_throttled,gpu_count
        if "describe-regions" in command:
            if throttled:
                throttled=False; return subprocess.CompletedProcess(command,255,b"",b"ThrottlingException")
            return subprocess.CompletedProcess(command,0,b'{"Regions":[{"RegionName":"us-east-1","OptInStatus":"opted-in"}]}',b"")
        if "describe-instance-type-offerings" in command:
            if offering_throttled:
                offering_throttled=False; return subprocess.CompletedProcess(command,255,b"",b"ThrottlingException")
            body={"InstanceTypeOfferings":[{"InstanceType":"g5.xlarge"}]}
        elif "describe-instance-types" in command:
            body={"InstanceTypes":[{"InstanceType":"g5.xlarge","GpuInfo":{"Gpus":[{"Name":"A10G","Count":gpu_count,"MemoryInfo":{"SizeInMiB":22888}}]},"VCpuInfo":{"DefaultVCpus":4},"MemoryInfo":{"SizeInMiB":16384}}]}
        else:
            stamp="2026-08-31T01:00:00Z" if "--next-token" in command else "2026-08-31T00:00:00Z"
            body={"SpotPriceHistory":[{"Timestamp":stamp,"AvailabilityZone":"us-east-1a","InstanceType":"g5.xlarge","ProductDescription":"Linux/UNIX","SpotPrice":"0.4"}]}
            if "--next-token" not in command: body["NextToken"]="next"
        return subprocess.CompletedProcess(command,0,json.dumps(body).encode(),b"")
    monkeypatch.setattr("market_data.collectors.aws_spot.time.sleep",lambda _:None)
    assert discover_regions("test",runner)==["us-east-1"]
    run=run_row(config,"aws_spot"); parsed,inserted,_=collect_all(config,run,runner=runner)
    assert parsed==2 and inserted>=2
    connection=connect(config.database_path)
    assert connection.execute("SELECT COUNT(*) FROM raw_retrievals WHERE run_id=?",(run,)).fetchone()[0]==5
    assert connection.execute("SELECT COUNT(*) FROM raw_retrievals WHERE run_id=? AND error_class IS NOT NULL",(run,)).fetchone()[0]==1
    assert connection.execute("SELECT COUNT(*) FROM gpu_instance_catalog").fetchone()[0]==1
    coverage=connection.execute("SELECT offered_instance_type_count,source_observation_count FROM aws_collection_coverage WHERE run_id=?",(run,)).fetchone()
    assert tuple(coverage)==(1,2)
    gpu_count=2
    run2=run_row(config,"aws_spot"); collect_all(config,run2,runner=runner)
    versions=connection.execute("SELECT effective_to_utc,gpu_count FROM gpu_instance_catalog ORDER BY effective_from_utc").fetchall()
    assert len(versions)==2 and versions[0][0] is not None and versions[1][1]==2
