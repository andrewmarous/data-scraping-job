from pathlib import Path
import pytest
from market_data.collectors.openrouter import completed_utc_day, parse_response, plan_ranges
from market_data.errors import SchemaError
from datetime import date, datetime, timezone

FIXTURE = Path(__file__).parents[1] / "fixtures" / "openrouter.json"

def test_parse_preserves_large_integer_and_other():
    parsed = parse_response(FIXTURE.read_bytes())
    assert parsed.records[0]["tokens"] == "123456789012345678901234567890"
    assert parsed.records[1]["is_other"] == 1
    assert parsed.meta["version"] == "1"

def test_parse_rejects_one_malformed_record():
    with pytest.raises(SchemaError):
        parse_response(b'{"data":[{"date":"2025-01-01"}],"meta":{}}')

def test_backfill_ranges_are_bounded():
    ranges = plan_ranges(date(2025,1,1), date(2025,2,2))
    assert [(p.start, p.end) for p in ranges] == [(date(2025,1,1),date(2025,1,31)),(date(2025,2,1),date(2025,2,2))]

def test_completed_day_uses_utc_across_dst_boundaries():
    assert completed_utc_day(datetime(2026,3,8,0,30,tzinfo=timezone.utc))==date(2026,3,7)
    assert completed_utc_day(datetime(2026,11,1,0,30,tzinfo=timezone.utc))==date(2026,10,31)
