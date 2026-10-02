from __future__ import annotations

import json
import os
import random
import time
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from market_data import __version__
from market_data.database import connect, utc_now
from market_data.errors import SchemaError
from market_data.raw_store import quarantine, store_raw

ENDPOINT = "https://console.vast.ai/api/v0/bundles/"
PARSER_VERSION = "1"
SOURCE_CONTRACT = {"on-demand": "on-demand", "interruptible": "bid", "reserved": "reserved"}


def _decimal(value: Any, field: str, *, required: bool = False) -> str | None:
    if value is None and not required:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise SchemaError(f"Vast field {field} is not numeric")
    try:
        return format(Decimal(str(value)), "f")
    except InvalidOperation as exc:
        raise SchemaError(f"Vast field {field} is not numeric") from exc


def parse_response(body: bytes, contract: str) -> list[dict[str, Any]]:
    try:
        value = json.loads(body, parse_float=Decimal)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SchemaError(f"Vast response is not valid JSON: {exc}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("offers"), list) or type(value.get("truncated")) is not bool:
        raise SchemaError("Vast response must contain offers and truncated")
    if value["truncated"]:
        raise SchemaError("Vast response is truncated")
    if not value["offers"]:
        raise SchemaError("Vast response unexpectedly contains no offers")
    records = []
    for index, offer in enumerate(value["offers"]):
        if not isinstance(offer, dict):
            raise SchemaError(f"Vast offer {index} is not an object")
        required = {"id", "gpu_name", "num_gpus", "dph_total"}
        if not required.issubset(offer):
            raise SchemaError(f"Vast offer {index} is missing {sorted(required-set(offer))}")
        if isinstance(offer["id"], bool) or not isinstance(offer["id"], (str, int)):
            raise SchemaError(f"Vast offer {index} has invalid id")
        if not isinstance(offer["gpu_name"], str) or not offer["gpu_name"]:
            raise SchemaError(f"Vast offer {index} has invalid gpu_name")
        if type(offer["num_gpus"]) is not int or offer["num_gpus"] <= 0:
            raise SchemaError(f"Vast offer {index} has invalid num_gpus")
        _decimal(offer["dph_total"], "dph_total", required=True)
        records.append(offer)
    return records


def _j(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def collect_contract(config, run_id: str, contract: str, *, client: httpx.Client | None = None, sleep=time.sleep) -> tuple[int, int, int]:
    if contract not in SOURCE_CONTRACT:
        raise SchemaError(f"Unsupported Vast contract: {contract}")
    parameters = {"verified":{"eq":True},"external":{"eq":False},"rentable":{"eq":True},"rented":{"eq":False},
                  "order":[["id","asc"]],"type":SOURCE_CONTRACT[contract],"limit":10000,"allocated_storage":5.0}
    own = client is None
    client = client or httpx.Client(timeout=config.vast_timeout, headers={
        "Authorization": f"Bearer {os.environ['VAST_API_KEY']}", "User-Agent": f"local-market-data/{__version__} ({config.contact})"})
    connection = connect(config.database_path)
    try:
        for attempt in range(1,6):
            retrieved_at, retrieval_id = utc_now(), str(uuid.uuid4())
            try:
                response = client.post(ENDPOINT, json=parameters); body=response.content
            except (httpx.TimeoutException,httpx.NetworkError) as exc:
                connection.execute("INSERT INTO raw_retrievals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(retrieval_id,run_id,"vast",ENDPOINT,_j(parameters),retrieved_at,None,None,"{}",None,None,None,__version__,PARSER_VERSION,attempt,type(exc).__name__,str(exc)[:1000]))
                connection.execute("UPDATE collection_runs SET attempts=? WHERE run_id=?",(attempt,run_id)); connection.commit()
                if attempt==5: raise
                sleep(random.uniform(0,min(2**attempt,60))); continue
            if len(body) > config.vast_max_bytes: raise SchemaError("Vast response exceeds configured size limit")
            raw=store_raw(config.data_dir,"vast",retrieved_at,run_id,body)
            connection.execute("INSERT INTO raw_retrievals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(retrieval_id,run_id,"vast",ENDPOINT,_j(parameters),retrieved_at,None,response.status_code,_j({k.lower():v for k,v in response.headers.items() if k.lower().startswith("x-ratelimit") or k.lower()=="retry-after"}),str(raw.path),raw.sha256,raw.size,__version__,PARSER_VERSION,attempt,None,None))
            connection.execute("UPDATE collection_runs SET attempts=?,retrieved_count=retrieved_count+1 WHERE run_id=?",(attempt,run_id)); connection.commit()
            if response.status_code in {408,429} or response.status_code>=500:
                if attempt==5: response.raise_for_status()
                retry=response.headers.get("Retry-After"); sleep(min(float(retry),300) if retry and retry.isdigit() else random.uniform(0,min(2**attempt,60))); continue
            response.raise_for_status(); break
        try: records = parse_response(body, contract)
        except SchemaError:
            quarantine(config.data_dir,"vast",raw.path); raise
        connection.execute("BEGIN")
        for offer in records:
            geo = offer.get("geolocation") or offer.get("geolocode")
            country = geo.split(",")[-1].strip() if isinstance(geo,str) and "," in geo else None
            connection.execute("INSERT INTO vast_offer_snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(
                str(uuid.uuid4()),retrieved_at,str(offer["id"]),str(offer.get("machine_id")) if offer.get("machine_id") is not None else None,
                str(offer.get("host_id")) if offer.get("host_id") is not None else None,contract,
                int(offer["rentable"]) if type(offer.get("rentable")) is bool else None,offer["gpu_name"],offer["num_gpus"],
                offer.get("gpu_ram") or offer.get("gpu_total_ram"),_decimal(offer.get("dph_total"),"dph_total"),_decimal(offer.get("min_bid"),"min_bid"),
                "USD",country,geo,offer.get("datacenter"),_decimal(offer.get("reliability"),"reliability"),str(offer.get("duration")) if offer.get("duration") is not None else None,
                _j({k:offer.get(k) for k in ("bw_nvlink","pcie_bw","pci_gen","gpu_lanes")}),
                _j({k:offer.get(k) for k in ("inet_up","inet_down","inet_up_cost","inet_down_cost")}),
                _j({k:offer.get(k) for k in ("disk_space","disk_bw","storage_cost")}),
                _j({k:offer.get(k) for k in ("dlperf","cpu_cores","cpu_ram","gpu_arch")}),_j(offer),retrieval_id))
        connection.commit()
        return len(records), len(records), 0
    finally:
        connection.close()
        if own: client.close()
