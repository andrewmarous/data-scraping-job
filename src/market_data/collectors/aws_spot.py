from __future__ import annotations

import json
import os
import subprocess
import random
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from market_data import __version__
from market_data.database import connect, utc_now
from market_data.errors import MarketDataError, SchemaError
from market_data.raw_store import quarantine, store_raw

PARSER_VERSION = "1"
AWS = "aws"
Runner = Callable[..., subprocess.CompletedProcess[bytes]]


def _run(profile: str | None, args: list[str], runner: Runner, failure_recorder=None) -> bytes:
    command = [AWS]
    if profile: command += ["--profile", profile]
    command += args + ["--output", "json", "--no-cli-pager"]
    for attempt in range(1,6):
        try: result=runner(command,capture_output=True,check=False)
        except OSError as exc: raise MarketDataError(f"Cannot execute AWS CLI: {exc}") from exc
        if not result.returncode: return result.stdout
        message=result.stderr.decode(errors="replace")[:1000]
        if failure_recorder: failure_recorder(attempt,result.stderr)
        retryable=any(word in message for word in ("Throttl","RequestLimitExceeded","ServiceUnavailable","InternalError","RequestTimeout"))
        if not retryable or attempt==5: raise MarketDataError(f"AWS CLI failed: {message}")
        time.sleep(random.uniform(0,min(2**attempt,60)))
    raise MarketDataError("AWS CLI retry budget exhausted")


def _object(body: bytes, required: str) -> dict[str, Any]:
    try: value = json.loads(body)
    except (UnicodeDecodeError,json.JSONDecodeError) as exc: raise SchemaError(f"AWS response is not valid JSON: {exc}") from exc
    if not isinstance(value,dict) or not isinstance(value.get(required),list):
        raise SchemaError(f"AWS response must contain {required}")
    return value


def discover_regions(profile: str | None, runner: Runner = subprocess.run) -> list[str]:
    body = _run(profile,["ec2","describe-regions","--all-regions"],runner)
    value = _object(body,"Regions")
    regions = sorted(row["RegionName"] for row in value["Regions"] if isinstance(row,dict) and isinstance(row.get("RegionName"),str)
                     and row.get("OptInStatus") in {"opt-in-not-required","opted-in"})
    if not regions: raise SchemaError("AWS returned no enabled regions")
    return regions


def parse_prices(body: bytes, region: str) -> tuple[list[dict[str,str]], str | None]:
    value = _object(body,"SpotPriceHistory")
    rows=[]
    for index,row in enumerate(value["SpotPriceHistory"]):
        if not isinstance(row,dict) or not {"Timestamp","AvailabilityZone","InstanceType","ProductDescription","SpotPrice"}.issubset(row):
            raise SchemaError(f"AWS Spot row {index} is incomplete")
        try: price=format(Decimal(str(row["SpotPrice"])),"f")
        except InvalidOperation as exc: raise SchemaError(f"AWS Spot row {index} has invalid price") from exc
        timestamp=str(row["Timestamp"])
        try:
            parsed_timestamp=datetime.fromisoformat(timestamp.replace("Z","+00:00"))
            if parsed_timestamp.tzinfo is None: raise ValueError("timestamp has no UTC offset")
            timestamp=parsed_timestamp.astimezone(timezone.utc).isoformat().replace("+00:00","Z")
        except ValueError as exc: raise SchemaError(f"AWS Spot row {index} has invalid timestamp") from exc
        rows.append({"timestamp":timestamp,"az":str(row["AvailabilityZone"]),"instance_type":str(row["InstanceType"]),
                     "product":str(row["ProductDescription"]),"price":price,"region":region})
    token=value.get("NextToken")
    if token is not None and not isinstance(token,str): raise SchemaError("AWS NextToken is invalid")
    return rows,token


def _archive(connection, config, run_id: str, region: str, operation: str, parameters: dict[str,Any], body: bytes, attempt: int, error: Exception | None = None) -> str:
    retrieval_id,retrieved_at=str(uuid.uuid4()),utc_now()
    raw=store_raw(config.data_dir,"aws_spot",retrieved_at,run_id,body)
    connection.execute("INSERT INTO raw_retrievals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(
        retrieval_id,run_id,"aws_spot",f"aws ec2 {operation}",json.dumps(parameters,sort_keys=True),retrieved_at,None,0,"{}",
        str(raw.path),raw.sha256,raw.size,__version__,PARSER_VERSION,attempt,type(error).__name__ if error else None,str(error)[:1000] if error else None))
    return retrieval_id


def _region_types(profile: str | None, region: str, families: tuple[str,...], runner: Runner) -> tuple[list[str],bytes]:
    body=_run(profile,["ec2","describe-instance-type-offerings","--region",region,"--location-type","region",
                       "--filters",*(["Name=instance-type,Values=" + ",".join(f+".*" for f in families)] )],runner)
    value=_object(body,"InstanceTypeOfferings")
    return sorted({row["InstanceType"] for row in value["InstanceTypeOfferings"] if isinstance(row,dict) and isinstance(row.get("InstanceType"),str)}),body


def parse_catalog(body: bytes) -> list[dict[str,Any]]:
    value=_object(body,"InstanceTypes"); rows=[]
    for index,item in enumerate(value["InstanceTypes"]):
        try:
            gpus=item["GpuInfo"]["Gpus"]
            if not gpus: continue
            names={str(gpu["Name"]) for gpu in gpus}; count=sum(int(gpu["Count"]) for gpu in gpus)
            memory=sum(int(gpu.get("MemoryInfo",{}).get("SizeInMiB",0))*int(gpu["Count"]) for gpu in gpus) or None
            rows.append({"instance_type":str(item["InstanceType"]),"accelerator_model":" + ".join(sorted(names)),"gpu_count":count,
                         "gpu_memory_mb":memory,"vcpu_count":int(item["VCpuInfo"]["DefaultVCpus"]),"ram_mb":int(item["MemoryInfo"]["SizeInMiB"]),
                         "topology":json.dumps({"gpus":gpus},sort_keys=True,separators=(",",":"))})
        except (KeyError,TypeError,ValueError) as exc: raise SchemaError(f"AWS instance type row {index} is incomplete") from exc
    return rows


def _normalize_catalog(connection, rows, retrieval_id, effective):
    inserted=0
    for row in rows:
        current=connection.execute("SELECT * FROM gpu_instance_catalog WHERE provider='aws' AND instance_type=? AND effective_to_utc IS NULL",(row['instance_type'],)).fetchone()
        prior_count=connection.execute("SELECT COUNT(*) FROM gpu_instance_catalog WHERE provider='aws' AND instance_type=?",(row['instance_type'],)).fetchone()[0]
        version_start = effective if current else "1970-01-01T00:00:00Z"
        signature=(row['accelerator_model'],row['gpu_count'],row['gpu_memory_mb'],row['topology'],row['vcpu_count'],row['ram_mb'])
        if current and signature==(current['accelerator_model'],current['gpu_count'],current['gpu_memory_mb'],current['topology_json'],current['vcpu_count'],current['ram_mb']):
            first_start="1970-01-01T00:00:00Z" if prior_count == 1 else version_start
            if current['effective_from_utc'] > first_start:
                connection.execute("UPDATE gpu_instance_catalog SET effective_from_utc=? WHERE catalog_id=?",(first_start,current['catalog_id']))
            continue
        if current: connection.execute("UPDATE gpu_instance_catalog SET effective_to_utc=? WHERE catalog_id=?",(effective,current['catalog_id']))
        connection.execute("INSERT INTO gpu_instance_catalog VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(str(uuid.uuid4()),'aws',row['instance_type'],version_start,None,row['accelerator_model'],row['gpu_count'],row['gpu_memory_mb'],row['topology'],row['vcpu_count'],row['ram_mb'],retrieval_id))
        inserted+=1
    return inserted


def collect_all(config, run_id: str, requested_region: str | None = None, *, runner: Runner = subprocess.run) -> tuple[int,int,int]:
    profile=os.environ.get("AWS_PROFILE") or config.aws_profile
    connection=connect(config.database_path); parsed=inserted=attempt=0
    end=datetime.now(timezone.utc); start=end-timedelta(hours=config.aws_overlap_hours)
    try:
        def call(operation,region,parameters,args):
            nonlocal attempt
            def record_failure(local_attempt,body):
                nonlocal attempt
                attempt+=1
                error=MarketDataError(body.decode(errors="replace")[:1000])
                _archive(connection,config,run_id,region,operation,parameters,body,attempt,error)
                connection.execute("UPDATE collection_runs SET attempts=?,retrieved_count=retrieved_count+1 WHERE run_id=?",(attempt,run_id)); connection.commit()
            body=_run(profile,args,runner,record_failure); attempt+=1
            retrieval_id=_archive(connection,config,run_id,region,operation,parameters,body,attempt)
            connection.execute("UPDATE collection_runs SET attempts=?,retrieved_count=retrieved_count+1 WHERE run_id=?",(attempt,run_id)); connection.commit()
            return body,retrieval_id
        if requested_region: regions=[requested_region]
        elif config.aws_regions: regions=list(config.aws_regions)
        else:
            body,_=call("describe-regions","global",{},["ec2","describe-regions","--all-regions"])
            value=_object(body,"Regions"); regions=sorted(row['RegionName'] for row in value['Regions'] if isinstance(row,dict) and row.get('OptInStatus') in {'opt-in-not-required','opted-in'})
            if not regions: raise SchemaError("AWS returned no enabled regions")
        cataloged_types=set()
        for region in regions:
            offer_parameters={"region":region,"families":config.aws_instance_families}
            offer_body,offer_retrieval=call("describe-instance-type-offerings",region,offer_parameters,["ec2","describe-instance-type-offerings","--region",region,"--location-type","region","--filters","Name=instance-type,Values="+",".join(f+".*" for f in config.aws_instance_families)])
            offer_value=_object(offer_body,"InstanceTypeOfferings")
            types=sorted({row["InstanceType"] for row in offer_value["InstanceTypeOfferings"] if isinstance(row,dict) and isinstance(row.get("InstanceType"),str)})
            family_counts={family:0 for family in config.aws_instance_families}; family_newest={family:None for family in config.aws_instance_families}
            new_types=[item for item in types if item not in cataloged_types]
            for offset in range(0,len(new_types),100):
                batch=new_types[offset:offset+100]
                catalog_body,catalog_retrieval=call("describe-instance-types",region,{"region":region,"instance_types":batch},["ec2","describe-instance-types","--region",region,"--instance-types",*batch])
                inserted += _normalize_catalog(connection,parse_catalog(catalog_body),catalog_retrieval,start.isoformat().replace("+00:00","Z")); connection.commit()
                cataloged_types.update(batch)
            # Keep command lines bounded and independently paginated.
            for offset in range(0,len(types),100):
                batch=types[offset:offset+100]; token=None
                while True:
                    args=["ec2","describe-spot-price-history","--region",region,"--start-time",start.isoformat(),"--end-time",end.isoformat(),
                          "--instance-types",*batch,"--product-descriptions","Linux/UNIX"]
                    if token: args += ["--next-token",token]
                    parameters={"region":region,"start":start.isoformat(),"end":end.isoformat(),"instance_types":batch,"next_token":token}
                    body,retrieval_id=call("describe-spot-price-history",region,parameters,args)
                    try: rows,token=parse_prices(body,region)
                    except SchemaError:
                        raw_path=connection.execute("SELECT body_path FROM raw_retrievals WHERE retrieval_id=?",(retrieval_id,)).fetchone()[0]
                        quarantine(config.data_dir,"aws_spot",__import__('pathlib').Path(raw_path)); raise
                    parsed += len(rows)
                    for row in rows:
                        family=row["instance_type"].split(".",1)[0]
                        if family in family_counts:
                            family_counts[family]+=1
                            family_newest[family]=max(family_newest[family] or row["timestamp"],row["timestamp"])
                        cursor=connection.execute("INSERT OR IGNORE INTO aws_spot_prices VALUES(?,?,?,?,?,?,?,?,?)",(
                            str(uuid.uuid4()),row["timestamp"],region,row["az"],row["instance_type"],row["product"],"USD",row["price"],retrieval_id))
                        inserted += cursor.rowcount
                    connection.commit()
                    if not token: break
            for family in config.aws_instance_families:
                offered=[item for item in types if item.startswith(family+".")]
                connection.execute("INSERT INTO aws_collection_coverage VALUES(?,?,?,?,?,?,?,?,?,?)",(str(uuid.uuid4()),run_id,utc_now(),region,family,json.dumps(offered,separators=(",",":")),len(offered),family_counts[family],family_newest[family],offer_retrieval))
            connection.commit()
        return parsed,inserted,0
    finally: connection.close()
