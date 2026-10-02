from __future__ import annotations

import json
import random
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

import httpx

from market_data import __version__
from market_data.database import connect, utc_now
from market_data.errors import SchemaError
from market_data.raw_store import quarantine, store_raw
from .base import Partition

ENDPOINT = "https://openrouter.ai/api/v1/datasets/rankings-daily"
COLLECTOR_VERSION = __version__
PARSER_VERSION = "1"

@dataclass(frozen=True)
class ParsedResponse:
    records: list[dict[str, Any]]
    meta: dict[str, Any]


def completed_utc_day(now: datetime | None = None) -> date:
    current = now or datetime.now(timezone.utc)
    return current.astimezone(timezone.utc).date() - timedelta(days=1)


def daily_window(end: date, overlap_days: int) -> Partition:
    return Partition(end - timedelta(days=overlap_days), end)


def plan_ranges(start: date, end: date, days_per_request: int = 31) -> list[Partition]:
    if start > end:
        raise ValueError("start date must not follow end date")
    result = []
    cursor = start
    while cursor <= end:
        part_end = min(end, cursor + timedelta(days=days_per_request - 1))
        result.append(Partition(cursor, part_end))
        cursor = part_end + timedelta(days=1)
    return result


def parse_response(body: bytes) -> ParsedResponse:
    try:
        value = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SchemaError(f"Response is not valid JSON: {exc}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("data"), list) or not isinstance(value.get("meta"), dict):
        raise SchemaError("Response must contain data array and meta object")
    meta = value["meta"]
    required_meta = {"as_of", "start_date", "end_date", "version"}
    if not required_meta.issubset(meta):
        raise SchemaError(f"Response meta is missing: {sorted(required_meta - set(meta))}")
    records = []
    for index, row in enumerate(value["data"]):
        if not isinstance(row, dict):
            raise SchemaError(f"Data row {index} is not an object")
        required = {"date", "model_permaslug", "total_tokens"}
        if not required.issubset(row):
            raise SchemaError(f"Data row {index} is missing: {sorted(required - set(row))}")
        try:
            observed_date = date.fromisoformat(row["date"])
        except (TypeError, ValueError) as exc:
            raise SchemaError(f"Data row {index} has an invalid date") from exc
        model = row["model_permaslug"]
        tokens = row["total_tokens"]
        if not isinstance(model, str) or not model or isinstance(tokens, bool):
            raise SchemaError(f"Data row {index} has an invalid model or token value")
        if isinstance(tokens, int):
            token_text = str(tokens)
        elif isinstance(tokens, str) and tokens.isascii() and tokens.isdigit():
            token_text = tokens
        else:
            raise SchemaError(f"Data row {index} has an invalid token value")
        if int(token_text) < 0:
            raise SchemaError(f"Data row {index} has a negative token value")
        records.append({"date": observed_date.isoformat(), "model": model, "tokens": token_text,
                        "is_other": int(model.lower() == "other")})
    return ParsedResponse(records, meta)


def _selected_headers(headers: httpx.Headers) -> dict[str, str]:
    names = {"content-type", "etag", "last-modified", "retry-after", "x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset"}
    return {key.lower(): value for key, value in headers.items() if key.lower() in names}


def _insert_retrieval(connection, *, retrieval_id, run_id, retrieved_at, parameters, status=None, headers=None,
                      raw=None, attempt=1, error=None):
    connection.execute("""INSERT INTO raw_retrievals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
        retrieval_id, run_id, "openrouter", ENDPOINT, json.dumps(parameters, sort_keys=True), retrieved_at,
        None, status, json.dumps(headers or {}, sort_keys=True), str(raw.path) if raw else None,
        raw.sha256 if raw else None, raw.size if raw else None, COLLECTOR_VERSION, PARSER_VERSION, attempt,
        type(error).__name__ if error else None, str(error)[:1000] if error else None))


def collect_partition(config, run_id: str, partition: Partition, *, client: httpx.Client | None = None,
                      sleep: Callable[[float], None] = time.sleep) -> tuple[int, int, int]:
    parameters = {"start_date": partition.start.isoformat(), "end_date": partition.end.isoformat()}
    own_client = client is None
    client = client or httpx.Client(timeout=config.openrouter_timeout, headers={
        "Authorization": f"Bearer {__import__('os').environ['OPENROUTER_API_KEY']}",
        "User-Agent": f"local-market-data/{__version__} ({config.contact})",
    })
    connection = connect(config.database_path)
    try:
        for attempt in range(1, 6):
            retrieval_id = str(uuid.uuid4())
            retrieved_at = utc_now()
            try:
                with client.stream("GET", ENDPOINT, params=parameters) as response:
                    chunks, size = [], 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > config.openrouter_max_bytes:
                            raise SchemaError("Response exceeds the configured size limit")
                        chunks.append(chunk)
                    body = b"".join(chunks)
                    raw = store_raw(config.data_dir, "openrouter", retrieved_at, run_id, body)
                    _insert_retrieval(connection, retrieval_id=retrieval_id, run_id=run_id, retrieved_at=retrieved_at,
                                      parameters=parameters, status=response.status_code, headers=_selected_headers(response.headers),
                                      raw=raw, attempt=attempt)
                    connection.execute("UPDATE collection_runs SET attempts=?, retrieved_count=retrieved_count+1 WHERE run_id=?", (attempt, run_id))
                    connection.commit()
                    if response.status_code in {408, 429} or response.status_code >= 500:
                        if attempt == 5:
                            response.raise_for_status()
                        retry_after = response.headers.get("Retry-After")
                        delay = min(float(retry_after), 300) if retry_after and retry_after.isdigit() else random.uniform(0, min(2 ** attempt, 60))
                        sleep(delay)
                        continue
                    response.raise_for_status()
                try:
                    parsed = parse_response(body)
                except SchemaError:
                    quarantine(config.data_dir, "openrouter", raw.path)
                    raise
                meta_json = json.dumps(parsed.meta, sort_keys=True, separators=(",", ":"))
                inserted = revised = 0
                connection.execute("BEGIN")
                for row in parsed.records:
                    current = connection.execute("""SELECT observation_id,total_tokens_text,is_other,source_meta_json
                        FROM openrouter_daily_tokens WHERE observation_date=? AND model_permaslug=? AND is_current=1""",
                        (row["date"], row["model"])).fetchone()
                    if current and current["total_tokens_text"] == row["tokens"] and current["is_other"] == row["is_other"]:
                        continue
                    observation_id = str(uuid.uuid4())
                    if current:
                        connection.execute("UPDATE openrouter_daily_tokens SET is_current=0 WHERE observation_id=?", (current["observation_id"],))
                        revised += 1
                    connection.execute("INSERT INTO openrouter_daily_tokens VALUES(?,?,?,?,?,?,?,?,?,1)",
                        (observation_id, row["date"], row["model"], row["tokens"], row["is_other"], meta_json,
                         retrieval_id, retrieved_at, current["observation_id"] if current else None))
                    inserted += 1
                connection.execute("UPDATE raw_retrievals SET source_as_of=? WHERE retrieval_id=?", (str(parsed.meta["as_of"]), retrieval_id))
                connection.commit()
                return len(parsed.records), inserted, revised
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                _insert_retrieval(connection, retrieval_id=retrieval_id, run_id=run_id, retrieved_at=retrieved_at,
                                  parameters=parameters, attempt=attempt, error=exc)
                connection.execute("UPDATE collection_runs SET attempts=? WHERE run_id=?", (attempt, run_id))
                connection.commit()
                if attempt == 5:
                    raise
                sleep(random.uniform(0, min(2 ** attempt, 60)))
        raise RuntimeError("retry loop ended unexpectedly")
    finally:
        connection.close()
        if own_client:
            client.close()
