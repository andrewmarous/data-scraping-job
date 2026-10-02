"""Read-only daily OpenRouter batch endpoint listings."""
from __future__ import annotations

import json
import logging
import os
import random
import time
import uuid
from datetime import date, timedelta
from urllib.parse import quote

import httpx

from market_data import __version__
from market_data.database import connect, utc_now
from market_data.errors import SchemaError
from market_data.raw_store import store_raw
from .openrouter import ENDPOINT as RANKINGS, parse_response, _selected_headers

SOURCE = 'openrouter_batch'
PARSER_VERSION = '1'
CATALOG = 'https://openrouter.ai/api/v1/models'
LOG = logging.getLogger('market_data')


def discovery(ranking: bytes, catalog: bytes, start: date, end: date):
    parsed = parse_response(ranking)
    if parsed.meta['start_date'] != start.isoformat() or parsed.meta['end_date'] != end.isoformat() or not isinstance(parsed.meta['as_of'], str):
        raise SchemaError('Ranking date bounds or as_of are invalid')
    totals = {}
    seen = set()
    for row in parsed.records:
        key = (row['date'], row['model'])
        if key in seen or not start <= date.fromisoformat(row['date']) <= end:
            raise SchemaError('Duplicate or out-of-window ranking row')
        seen.add(key)
        totals[row['model']] = totals.get(row['model'], 0) + int(row['tokens'])
    try:
        value = json.loads(catalog)
    except (ValueError, UnicodeError) as exc:
        raise SchemaError('Invalid catalog JSON') from exc
    if not isinstance(value, dict) or not isinstance(value.get('data'), list):
        raise SchemaError('Catalog must contain data array')
    models = {}
    malformed = []
    for item in value['data']:
        if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not isinstance(item.get('canonical_slug'), str) or '/' not in item['id'] or not item['id'].split('/', 1)[0]:
            malformed.append(str(item)[:300])
            continue
        if item['id'].endswith(':batch'):
            models[item['id']] = item['canonical_slug']
    matched = [(model, slug, totals[slug]) for model, slug in models.items() if slug in totals]
    matched.sort(key=lambda row: (-row[2], row[0]))
    unmatched = sorted(set(totals) - set(models.values()))
    return matched[:10], unmatched, malformed, parsed.meta['as_of']


def endpoints(body: bytes):
    try:
        value = json.loads(body)
    except (ValueError, UnicodeError) as exc:
        raise SchemaError('Invalid endpoints JSON') from exc
    if not isinstance(value, dict) or not isinstance(value.get('data'), dict) or not isinstance(value['data'].get('endpoints'), list):
        raise SchemaError('Endpoints must contain data.endpoints array')
    result = []
    seen = set()
    for item in value['data']['endpoints']:
        if not isinstance(item, dict) or not isinstance(item.get('provider_name'), str) or not item['provider_name'] or not isinstance(item.get('tag'), str):
            raise SchemaError('Invalid endpoint provider or tag')
        key = item['provider_name'], item['tag']
        if key in seen:
            raise SchemaError('Duplicate provider/tag in endpoints')
        seen.add(key)
        result.append(item)
    return result


def collect(config, run_id: str, day: date, *, client=None, sleep=time.sleep):
    own = client is None
    client = client or httpx.Client(timeout=config.openrouter_timeout, headers={
        'Authorization': f"Bearer {os.environ['OPENROUTER_API_KEY']}",
        'User-Agent': f'local-market-data/{__version__} ({config.contact})'})
    db = connect(config.database_path)
    attempts = 0

    def fetch(url, params=None):
        nonlocal attempts
        last = None
        for attempt in range(1, 4):
            attempts += 1
            rid, now = str(uuid.uuid4()), utc_now()
            status = None
            headers = {}
            raw = None
            body = None
            error = None
            try:
                with client.stream('GET', url, params=params) as response:
                    status = response.status_code
                    headers = _selected_headers(response.headers)
                    chunks, size = [], 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > config.openrouter_max_bytes:
                            raise SchemaError('Response exceeds size limit')
                        chunks.append(chunk)
                    body = b''.join(chunks)
                    raw = store_raw(config.data_dir, SOURCE, now, run_id, body)
            except (httpx.RequestError, SchemaError) as exc:
                error = exc
            db.execute('INSERT INTO raw_retrievals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (rid, run_id, SOURCE, url, json.dumps(params or {}), now, None, status, json.dumps(headers),
                        str(raw.path) if raw else None, raw.sha256 if raw else None, raw.size if raw else None,
                        __version__, PARSER_VERSION, attempt, type(error).__name__ if error else None, str(error)[:500] if error else None))
            db.execute('UPDATE collection_runs SET attempts=?,retrieved_count=retrieved_count+? WHERE run_id=?',
                       (attempts, int(raw is not None), run_id))
            db.commit()
            last = (rid, now, status, body, error)
            if error is None and status not in (408, 429) and status < 500:
                break
            if attempt < 3:
                sleep(random.uniform(0, min(2 ** attempt, 10)))
        return last

    def review(value, reason):
        now = utc_now()
        db.execute('INSERT INTO batch_discovery_reviews VALUES(?,?,?,?) ON CONFLICT(value,reason) DO UPDATE SET last_seen_at_utc=excluded.last_seen_at_utc', (value, reason, now, now))
        LOG.warning('Batch discovery review required: %s %s', reason, value)

    failures = []
    count = 0
    start = day - timedelta(days=7)
    end = day - timedelta(days=1)
    try:
        catalog = fetch(CATALOG)
        ranking = fetch(RANKINGS, {'start_date': start.isoformat(), 'end_date': end.isoformat()})
        if all(result[4] is None and result[2] == 200 for result in (catalog, ranking)):
            try:
                leaders, unmatched, malformed, as_of = discovery(ranking[3], catalog[3], start, end)
                sid = str(uuid.uuid4())
                db.execute('INSERT INTO batch_ranking_snapshots VALUES(?,?,?,?,?,?,?,?)',
                           (sid, run_id, day.isoformat(), start.isoformat(), end.isoformat(), as_of, ranking[0], catalog[0]))
                db.execute('UPDATE raw_retrievals SET source_as_of=? WHERE retrieval_id=?', (as_of, ranking[0]))
                for rank, (model, slug, tokens) in enumerate(leaders, 1):
                    prefix = model.split('/', 1)[0]
                    allowed = int(prefix.lower() not in {'openai', 'anthropic', 'google'})
                    db.execute('INSERT INTO batch_ranking_entries VALUES(?,?,?,?,?,?)', (sid, rank, model, slug, str(tokens), allowed))
                    if allowed:
                        db.execute("INSERT OR IGNORE INTO batch_tracked_models(model_id,canonical_slug,publisher_prefix,first_seen_at_utc) VALUES(?,?,?,?)", (model, slug, prefix, utc_now()))
                for slug in unmatched:
                    review(slug, 'unmatched_ranking_slug')
                for item in malformed:
                    review(item, 'malformed_catalog_identifier')
                db.commit()
            except (SchemaError, ValueError) as exc:
                failures.append(f'discovery parsing: {exc}')
                LOG.error('Batch discovery parsing failed: %s', exc)
        else:
            failures.append('discovery HTTP failure')
            LOG.error('Batch discovery HTTP failure')
        models = db.execute("SELECT model_id FROM batch_tracked_models WHERE tracking_state='active' ORDER BY model_id").fetchall()
        for row in models:
            model = row['model_id']
            url = CATALOG + '/' + quote(model, safe='/') + '/endpoints'
            rid, now, status, body, error = fetch(url)
            outcome = 'network_error' if error else 'http_error' if status != 200 else 'success'
            items = []
            if outcome == 'success':
                try:
                    items = endpoints(body)
                    if not items:
                        outcome = 'empty'
                except SchemaError:
                    outcome = 'parse_error'
            poll = str(uuid.uuid4())
            db.execute('INSERT INTO batch_endpoint_polls VALUES(?,?,?,?,?,?,?)',
                       (poll, run_id, model, rid, now, outcome, len(items) if outcome in ('success', 'empty') else None))
            for item in items:
                db.execute('INSERT INTO batch_endpoint_observations VALUES(?,?,?,?,?,?,?,?)',
                           (poll, model, now, item['provider_name'], item['tag'], json.dumps(item.get('status')),
                            json.dumps(item.get('pricing')), json.dumps(item, sort_keys=True)))
            db.commit()
            if outcome not in ('success', 'empty'):
                failures.append(f'{model}: {outcome}')
                LOG.error('Batch endpoint polling failed: %s %s', model, outcome)
            count += len(items)
        if failures:
            raise RuntimeError('; '.join(failures)[:1000])
        return len(models), count, 0
    finally:
        db.close()
        if own:
            client.close()
