from __future__ import annotations

import json
import os
import random
import subprocess
import re
import time
import uuid
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from market_data import __version__
from market_data.database import connect, utc_now
from market_data.errors import SchemaError
from market_data.raw_store import quarantine, store_raw

PARSER_VERSION = "4"
SOURCES = {"runpod", "gcore", "clore", "saladcloud", "nebius", "azure", "google_cloud", "coreweave", "lambda"}


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _text(value: Any) -> str | None:
    return None if value is None else str(value)


def _integer(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _requests(config, source: str) -> list[tuple[str, dict[str, str], dict[str, Any]]]:
    contact = f"local-market-data/{__version__} ({config.contact})"
    if source == "lambda":
        return [("https://cloud.lambda.ai/api/v1/instance-types", {"Authorization": f"Bearer {os.environ['LAMBDA_API_KEY']}", "Accept": "application/json", "User-Agent": contact}, {})]
    if source == "runpod":
        return [("https://api.runpod.io/v2/catalog/gpus", {"Authorization": f"Bearer {os.environ['RUNPOD_API_KEY']}", "User-Agent": contact}, {"include":"AVAILABILITY", "product":"POD", "count":count}) for count in (1, 2, 4, 8)]
    if source == "clore":
        return [("https://api.clore.ai/v1/marketplace", {"auth": os.environ["CLORE_API_KEY"], "User-Agent": contact}, {})]
    if source == "saladcloud":
        url = f"https://api.salad.com/api/public/organizations/{config.salad_organization}/gpu-classes"
        return [(url, {"Salad-Api-Key": os.environ["SALAD_API_KEY"], "User-Agent": contact}, {}),
                ("https://salad.com/pricing", {"User-Agent":contact}, {})]
    if source == "gcore":
        return [(f"https://api.gcore.com/cloud/v3/gpu/baremetal/{config.gcore_project_id}/{region}/flavors", {"Authorization": f"APIKey {os.environ['GCORE_API_KEY']}", "User-Agent": contact}, {}) for region in config.gcore_region_ids]
    if source == "azure":
        return [("https://prices.azure.com/api/retail/prices", {"User-Agent": contact}, {"api-version":"2023-01-01-preview", "$filter":"serviceName eq 'Virtual Machines' and priceType eq 'Consumption' and contains(meterName, 'Spot') and contains(armSkuName, 'Standard_N')"})]
    if source == "google_cloud":
        return [("https://cloudbilling.googleapis.com/v1/services/6F81-5844-456A/skus", {"User-Agent": contact}, {"key":os.environ["GOOGLE_CLOUD_API_KEY"], "currencyCode":"USD", "pageSize":5000})]
    if source == "nebius":
        return [("https://docs.nebius.com/compute/resources/pricing", {"User-Agent": contact}, {})]
    if source == "coreweave":
        return [("https://www.coreweave.com/pricing", {"User-Agent": contact}, {})]
    raise SchemaError(f"Unsupported provider catalog: {source}")


def parse_response(source: str, body: bytes, content_type: str = "application/json", *, shape_count: int | None = None) -> list[dict[str, Any]]:
    if source in {"nebius", "coreweave"} or (source == "saladcloud" and "json" not in content_type.lower()):
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SchemaError(f"{source} pricing document is not UTF-8") from exc
        if len(text) < 100 or not re.search(r"price|spot|preempt", text, re.I):
            raise SchemaError(f"{source} pricing document has an unexpected shape")
        return [{"source_key":"pricing-document", "document_bytes":len(body)}]
    try:
        value = json.loads(body, parse_float=Decimal)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SchemaError(f"{source} response is not valid JSON: {exc}") from exc
    if not isinstance(value, (dict, list)):
        raise SchemaError(f"{source} response must be an object or list")
    if source == "azure":
        rows = value.get("Items") if isinstance(value, dict) else None
        if not isinstance(rows, list): raise SchemaError("Azure response has no Items list")
        return [r for r in rows if isinstance(r, dict) and "spot" in _text(r.get("skuName", "")).lower() and re.search(r"(^|[_ ])N[CDV]|GPU|H100|A100|L40", _text(r.get("armSkuName") or r.get("productName") or ""), re.I)]
    if source == "google_cloud":
        rows = value.get("skus") if isinstance(value, dict) else None
        if not isinstance(rows, list): raise SchemaError("Google Cloud response has no skus list")
        return [r for r in rows if isinstance(r, dict) and re.search(r"GPU", _text(r.get("description") or ""), re.I) and re.search(r"Spot|Preemptible", _text(r.get("description") or ""), re.I)]
    if source == "lambda":
        data = value.get("data") if isinstance(value, dict) else None
        if not isinstance(data, dict): raise SchemaError("Lambda response has no data object")
        result = []
        for sku, entry in data.items():
            if not isinstance(entry, dict) or not isinstance(entry.get("instance_type"), dict) or not isinstance(entry.get("regions_with_capacity_available"), list):
                raise SchemaError("Lambda response has malformed instance type")
            regions = entry["regions_with_capacity_available"]
            if any(not isinstance(r, dict) or not isinstance(r.get("name"), str) for r in regions):
                raise SchemaError("Lambda response has malformed region")
            result.extend({"sku": sku, "region": r["name"], "instance_type": entry["instance_type"], "capacity": True} for r in regions)
            if not regions: result.append({"sku": sku, "region": None, "instance_type": entry["instance_type"], "capacity": None})
        return result
    key = {"runpod":"data", "clore":"servers", "saladcloud":"items", "gcore":"results"}[source]
    rows = value.get(key) if isinstance(value, dict) else value
    if source == "runpod" and isinstance(value, dict) and not isinstance(rows, list):
        data = value.get("data")
        rows = (data.get("items") if isinstance(data, dict) else None)
        if rows is None: rows = (data.get("gpus") if isinstance(data, dict) else value.get("gpus"))
        if rows is None: rows = value.get("items")
    if not isinstance(rows, list): raise SchemaError(f"{source} response has no {key} list")
    if any(not isinstance(row, dict) for row in rows): raise SchemaError(f"{source} response contains non-object rows")
    if source == "runpod":
        expanded = []
        for row in rows:
            if not isinstance(row.get("price"), dict):
                expanded.append({**row, "_query_count": shape_count})
                continue
            max_counts = row.get("maxCount") or {}
            if not isinstance(max_counts, dict): raise SchemaError("Runpod maxCount is malformed")
            centers = row.get("dataCenters") or []
            if not isinstance(centers, list) or any(not isinstance(c, dict) or not isinstance(c.get("id"), str) for c in centers):
                raise SchemaError("Runpod dataCenters is malformed")
            for tier in ("secure", "community"):
                if row.get(tier) is True or (max_counts.get(tier) or 0) > 0:
                    # The regional status does not identify a tier. Keep it as
                    # source context, not tier-specific live capacity.
                    if centers:
                        expanded.extend({**row, "_market_tier": tier, "_query_count": shape_count,
                                         "_region": c["id"], "_region_status": c.get("availability")}
                                        for c in centers)
                    else:
                        expanded.append({**row, "_market_tier": tier, "_query_count": shape_count})
        return expanded
    return rows


def _normalized(source: str, row: dict[str, Any], region_hint: str | None) -> tuple[Any, ...]:
    if source == "azure":
        key = row.get("meterId") or row.get("skuId"); evidence="catalog_price"; contract="spot"
        product=row.get("armSkuName") or row.get("skuName"); gpu=product; region=row.get("armRegionName")
        available=None; availability=None; price=row.get("retailPrice"); currency=row.get("currencyCode"); unit=row.get("unitOfMeasure")
    elif source == "google_cloud":
        key=row.get("name"); evidence="catalog_price"; contract="spot"; product=row.get("description"); gpu=product
        regions=(row.get("serviceRegions") or []); region=",".join(regions) if isinstance(regions,list) else _text(regions)
        available=None; availability=None; price=_json(row.get("pricingInfo")); currency="USD"; unit="source pricing expression"
    elif source == "lambda":
        spec=row["instance_type"]; key=f"{row['sku']}:{row['region'] or 'no-region'}"
        evidence="live_capacity" if row["capacity"] else "product_catalog"
        contract="on-demand"; product=row["sku"]; gpu=spec.get("gpu_description")
        region=row["region"]; available=None
        availability="capacity available" if row["capacity"] else "no regions with capacity reported"
        cents=spec.get("price_cents_per_hour")
        price=str(Decimal(str(cents))/100) if cents is not None else None
        currency="USD" if price is not None else None; unit="instance hour" if price is not None else None
    elif source == "runpod":
        tier=row.get("_market_tier")
        key=f"{row.get('id') or row.get('gpuTypeId') or row.get('displayName')}:{tier or 'unspecified'}:{row.get('_query_count') or 'unknown'}:{row.get('_region') or 'unknown'}"
        contract="on-demand" if tier in {"secure", "community"} else "unknown"
        product=row.get("displayName") or row.get("gpuName") or row.get("id") or row.get("name")
        gpu=product; region=row.get("_region")
        # maxCount is a supported configuration size, not free inventory.
        # The catalog's LOW/HIGH status is not a count at a specific shape.
        available=None; availability=_text(row.get("_region_status") or (row.get("availability") if isinstance(row.get("availability"), str) else row.get("stockStatus")))
        evidence="product_catalog"
        rates=row.get("price")
        price=rates.get(tier) if isinstance(rates,dict) and tier else None
        currency="USD" if price is not None else None
        unit="GPU hour" if price is not None else None
    elif source == "clore":
        key=row.get("id"); evidence="live_offer"; contract="spot"; specs=row.get("specs") or {}
        product=(specs.get("gpu") if isinstance(specs,dict) else None); gpu=product; region=(specs.get("country") if isinstance(specs,dict) else None)
        partial=row.get("partial_gpu_rental") or {}; available=_integer(partial.get("available_gpus") if isinstance(partial,dict) else None)
        availability="rented" if row.get("rented") else "listed"; price=(row.get("price") or {}).get("spot") if isinstance(row.get("price"),dict) else row.get("spot"); currency=None; unit="server day"
    elif source == "saladcloud":
        key=row.get("id") or row.get("name") or row.get("source_key"); evidence="source_document" if row.get("source_key") else "product_catalog"; contract="interruptible-container"; product=row.get("name") or "published pricing document"; gpu=row.get("name"); region=None
        available=None; availability="retrieved" if row.get("source_key") else ("high-demand" if row.get("is_high_demand") else "listed"); price=None; currency=None; unit=None
    elif source == "gcore":
        key=f"{region_hint}:{row.get('name')}"; evidence="live_capacity"; contract="spot" if "spot" in _text(row.get("name", "")).lower() else "on-demand"
        props=row.get("hardware_properties") or {}; product=row.get("name"); gpu=props.get("gpu_model") if isinstance(props,dict) else None; region=region_hint
        available=_integer(row.get("capacity")); availability="available" if available else "unavailable"; price=row.get("price"); currency=row.get("currency"); unit=row.get("price_unit")
    else:
        key=row["source_key"]; evidence="source_document"; contract="spot"; product="published pricing document"; gpu=None; region=None; available=None; availability="retrieved"; price=None; currency=None; unit=None
    if key is None: key = uuid.uuid5(uuid.NAMESPACE_URL, _json(row)).hex
    return (_text(key), evidence, contract, _text(product), _text(gpu), (_integer(row['instance_type'].get('specs', {}).get('gpus')) if source == 'lambda' else _integer(row.get('_query_count')) if source == "runpod" else _integer(row.get("gpu_count") or row.get("gpuCount"))), _integer(row.get("gpu_memory_mb")), _text(region), _text(availability), available, _text(price), _text(currency), _text(unit))


def collect_source(config, run_id: str, source: str, *, client: httpx.Client | None = None, sleep=time.sleep) -> tuple[int,int,int]:
    if source not in SOURCES: raise SchemaError(f"Unsupported provider catalog: {source}")
    own=client is None; client=client or httpx.Client(timeout=config.provider_catalog_timeout, follow_redirects=True)
    connection=connect(config.database_path); total=inserted=0
    try:
        pending = list(_requests(config, source))
        pages = 0
        while pending:
            endpoint,headers,params = pending.pop(0)
            pages += 1
            if pages > 100:
                raise SchemaError(f"{source} pagination exceeded 100 pages")
            for attempt in range(1,6):
                retrieved_at,retrieval_id=utc_now(),str(uuid.uuid4())
                try:
                    if source == "lambda" and own:
                        # Lambda's bot protection rejects some Python HTTP clients.
                        proc=subprocess.run(["curl", "--silent", "--show-error", "--max-time", str(int(config.provider_catalog_timeout)), "--max-filesize", str(config.provider_catalog_max_bytes), "-H", "Authorization: " + headers["Authorization"], "-H", "Accept: application/json", "-w", "\\n%{http_code}", endpoint], capture_output=True, timeout=config.provider_catalog_timeout+5)
                        if proc.returncode: raise httpx.NetworkError(f"Lambda curl failed (exit {proc.returncode})")
                        payload, sep, status=proc.stdout.rpartition(b"\n")
                        if not sep or not status.isdigit(): raise SchemaError("Lambda curl returned no HTTP status")
                        response=httpx.Response(int(status), content=payload, request=httpx.Request("GET",endpoint))
                    else:
                        response=client.get(endpoint,headers=headers,params=params)
                    body=response.content
                except (httpx.TimeoutException,httpx.NetworkError) as exc:
                    connection.execute("INSERT INTO raw_retrievals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(retrieval_id,run_id,source,endpoint,_json(params),retrieved_at,None,None,"{}",None,None,None,__version__,PARSER_VERSION,attempt,type(exc).__name__,str(exc)[:1000])); connection.commit()
                    if attempt==5: raise
                    sleep(random.uniform(0,min(2**attempt,60))); continue
                if len(body)>config.provider_catalog_max_bytes: raise SchemaError(f"{source} response exceeds configured size limit")
                raw=store_raw(config.data_dir,source,retrieved_at,run_id,body)
                connection.execute("INSERT INTO raw_retrievals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(retrieval_id,run_id,source,endpoint,_json(params),retrieved_at,None,response.status_code,_json({k.lower():v for k,v in response.headers.items() if k.lower() in {"retry-after","x-ratelimit-limit","x-ratelimit-remaining"}}),str(raw.path),raw.sha256,raw.size,__version__,PARSER_VERSION,attempt,None,None)); connection.commit()
                if response.status_code in {408,429} or response.status_code>=500:
                    if attempt==5: response.raise_for_status()
                    retry=response.headers.get("Retry-After")
                    if retry and retry.isdigit(): sleep(min(float(retry),300))
                    elif response.status_code == 429: sleep(attempt * 10 + random.uniform(0, 5))
                    else: sleep(random.uniform(0,min(2**attempt,60)))
                    continue
                response.raise_for_status(); break
            try: rows=parse_response(source,body,response.headers.get("content-type",""), shape_count=params.get("count") if source == "runpod" else None)
            except SchemaError: quarantine(config.data_dir,source,raw.path); raise
            if source in {"azure", "google_cloud"}:
                page = json.loads(body)
                if source == "azure" and page.get("NextPageLink"):
                    # Azure omits the original filter from NextPageLink. Keep
                    # the verified GPU Spot filter and copy only its skip token.
                    skip = parse_qs(urlparse(page["NextPageLink"]).query).get("$skip", [None])[0]
                    if skip is None: raise SchemaError("Azure NextPageLink has no $skip token")
                    base_endpoint,base_headers,base_params = _requests(config,"azure")[0]
                    pending.append((base_endpoint,base_headers,{**base_params,"$skip":skip}))
                if source == "google_cloud" and page.get("nextPageToken"):
                    pending.append((endpoint, headers, {**params, "pageToken":page["nextPageToken"]}))
                if pending:
                    sleep(1)
            region_hint=endpoint.rstrip("/").split("/")[-2] if source=="gcore" else None
            connection.execute("BEGIN")
            for row in rows:
                fields=_normalized(source,row,region_hint)
                cursor=connection.execute("INSERT OR IGNORE INTO provider_market_observations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(str(uuid.uuid4()),source,retrieved_at,*fields[:5],fields[5],fields[6],*fields[7:13],_json({k:v for k,v in row.items() if k not in {"token","api_key"}}),_json(row),retrieval_id))
                inserted += cursor.rowcount
            connection.commit(); total += len(rows)
        return total,inserted,0
    except Exception:
        # A paginated snapshot is useful only when every page succeeds. Raw
        # retrievals remain as evidence, but normalized rows stay all-or-none.
        connection.rollback()
        connection.execute("DELETE FROM provider_market_observations WHERE retrieval_id IN (SELECT retrieval_id FROM raw_retrievals WHERE run_id=?)", (run_id,))
        connection.commit()
        raise
    finally:
        connection.close()
        if own: client.close()
