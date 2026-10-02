import json
from datetime import date

import pytest

from market_data.collectors.openrouter_batch import discovery, endpoints
from market_data.errors import SchemaError


def ranking(rows):
    return json.dumps({'data': rows, 'meta': {'as_of': 'today', 'version': 'v1', 'start_date': '2026-01-01', 'end_date': '2026-01-07'}}).encode()


def row(model, tokens, day='2026-01-01'):
    return {'model_permaslug': model, 'total_tokens': tokens, 'date': day}


def catalog():
    return json.dumps({'data': [
        {'id': 'foo/a', 'canonical_slug': 'foo/a'},
        {'id': 'foo/a:batch', 'canonical_slug': 'foo/a'},
        {'id': 'foo/b:batch', 'canonical_slug': 'foo/b'},
        {'id': 'openai/c:batch', 'canonical_slug': 'openai/c'},
    ]}).encode()


def discover(rows):
    return discovery(ranking(rows), catalog(), date(2026, 1, 1), date(2026, 1, 7))


def test_join_totals_ties_and_unmatched():
    leaders, unmatched, malformed, as_of = discover([row('foo/a', '2'), row('foo/a', '3', '2026-01-02'), row('foo/b', '5'), row('missing/x', '8')])
    assert leaders == [('foo/a:batch', 'foo/a', 5), ('foo/b:batch', 'foo/b', 5)]
    assert unmatched == ['missing/x']
    assert not malformed
    assert as_of == 'today'


@pytest.mark.parametrize('rows', [[row('foo/a', '1'), row('foo/a', '2')], [row('foo/a', '-1')], [row('foo/a', 'abc')]])
def test_bad_rankings(rows):
    with pytest.raises(SchemaError):
        discover(rows)


def test_endpoint_variants_and_empty_and_invalid():
    items = [{'provider_name': 'P', 'tag': 'one', 'status': 0, 'pricing': {'prompt': '1'}}, {'provider_name': 'P', 'tag': 'two'}]
    assert endpoints(json.dumps({'data': {'endpoints': items}}).encode()) == items
    assert endpoints(b'{"data":{"endpoints":[]}}') == []
    with pytest.raises(SchemaError):
        endpoints(b'not json')
    with pytest.raises(SchemaError):
        endpoints(b'{"data":{"endpoints":{}}}')
