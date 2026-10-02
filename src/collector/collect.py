"""One explicit source, parser, and transactional collection lifecycle."""
from datetime import datetime, timezone
import json
import math
import os
import time
import uuid

import httpx

from . import database, raw_store
from .config import Config
from .locking import lock
from .logging import event, now

PARSER_VERSION = "1"


class SchemaError(ValueError):
    pass


class RetryableError(RuntimeError):
    pass


def fetch(config: Config) -> tuple[bytes, str]:
    if not config.url:
        with config.fixture.open("rb") as stream:
            body = stream.read(config.max_response_bytes + 1)
        if len(body) > config.max_response_bytes:
            raise ValueError("Response exceeds byte limit")
        return body, "application/json"
    headers = {"Accept": "application/json"}
    token = os.environ.get("COLLECTOR_TOKEN")
    if token:
        headers["Authorization"] = "Bearer " + token
    # GET only. Never follow a redirect with credentials to another host.
    with httpx.Client(timeout=config.timeout_seconds, follow_redirects=False) as client:
        for attempt in range(config.retries + 1):
            try:
                with client.stream("GET", config.url, headers=headers) as response:
                    if response.status_code == 429 or response.status_code in (500, 502, 503, 504):
                        raise RetryableError("Transient source response")
                    response.raise_for_status()
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > config.max_response_bytes:
                            raise ValueError("Response exceeds byte limit")
                    return bytes(body), response.headers.get("content-type", "application/octet-stream")
            except (httpx.TransportError, RetryableError):
                if attempt == config.retries:
                    raise
                time.sleep(min(2 ** attempt, 30))
    raise AssertionError("Unreachable")


def parse(body: bytes) -> list[tuple[str, str, float]]:
    try:
        values = json.loads(body)
        if not isinstance(values, list):
            raise SchemaError("Expected a JSON array")
        records, keys = [], set()
        for value in values:
            if not isinstance(value, dict) or set(value) != {"station", "measured_at", "temperature_c"}:
                raise SchemaError("Unexpected record fields")
            station = value["station"]
            if not isinstance(station, str) or not station.strip():
                raise SchemaError("Station is required")
            moment = datetime.fromisoformat(value["measured_at"].replace("Z", "+00:00"))
            if moment.tzinfo is None:
                raise SchemaError("Timestamp must include a timezone")
            timestamp = moment.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
            temperature = value["temperature_c"]
            if type(temperature) not in (int, float) or not math.isfinite(temperature):
                raise SchemaError("Temperature must be finite")
            key = (station, timestamp)
            if key in keys:
                raise SchemaError("Duplicate measurement key")
            keys.add(key)
            records.append((station, timestamp, float(temperature)))
        return records
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as error:
        raise SchemaError("Invalid measurements") from error


def initialize(config: Config):
    with lock(config.data_dir):
        db = database.connect(config.data_dir)
        try:
            database.migrate(db, config.root / "migrations")
        finally:
            db.close()


def collect(config: Config, retrieval_id: str | None = None) -> str:
    with lock(config.data_dir):
        db = database.connect(config.data_dir)
        run_id = str(uuid.uuid4())
        raw_id = None
        try:
            database.migrate(db, config.root / "migrations")
            # We hold the exclusive collection lock, so older running rows are stale.
            db.execute("UPDATE runs SET status='interrupted',finished_at=? WHERE status='running'", (now(),))
            db.execute("INSERT INTO runs(id,started_at,status,parser_version,replay) VALUES(?,?,?,?,?)",
                       (run_id, now(), "running", PARSER_VERSION, int(retrieval_id is not None)))
            db.commit()
            event(config.data_dir, "run_started", run_id)
            try:
                if retrieval_id is not None:
                    row = db.execute("SELECT * FROM retrievals WHERE id=?", (retrieval_id,)).fetchone()
                    if row is None:
                        raise ValueError("Unknown retrieval ID")
                    raw_id = retrieval_id
                    body = raw_store.read(config.data_dir, row, config.max_response_bytes)
                else:
                    body, content_type = fetch(config)
                    raw_id = str(uuid.uuid4())
                    path, digest = raw_store.store(config.data_dir, raw_id, body)
                    db.execute("INSERT INTO retrievals VALUES(?,?,?,?,?,?,?)",
                               (raw_id, run_id, now(), path, digest, len(body), content_type))
                    db.commit()
                records = parse(body)
                with db:
                    database.write_measurements(db, records, raw_id)
                    if records and retrieval_id is None:
                        # This snapshot job rereads all records. The watermark is informational,
                        # not a pagination cursor. Never move it backwards.
                        watermark = max(record[1] for record in records)
                        db.execute("INSERT INTO checkpoint VALUES(1,?) ON CONFLICT(id) DO UPDATE SET "
                                   "measured_at=max(checkpoint.measured_at,excluded.measured_at)", (watermark,))
                    db.execute("UPDATE runs SET status='succeeded',finished_at=?,retrieval_id=?,record_count=? WHERE id=?",
                               (now(), raw_id, len(records), run_id))
            except BaseException as error:
                db.rollback()
                state = "quarantined" if isinstance(error, SchemaError) else "failed"
                db.execute("UPDATE runs SET status=?,finished_at=?,retrieval_id=?,error_class=? WHERE id=?",
                           (state, now(), raw_id, type(error).__name__, run_id))
                db.commit()
                if state == "quarantined" and raw_id:
                    directory = config.data_dir / "quarantine"
                    directory.mkdir(exist_ok=True)
                    (directory / (run_id + ".json")).write_text(json.dumps({"retrieval_id": raw_id}))
                event(config.data_dir, "run_" + state, run_id, error_class=type(error).__name__)
                raise
            event(config.data_dir, "run_succeeded", run_id, record_count=len(records))
            return run_id
        finally:
            db.close()
