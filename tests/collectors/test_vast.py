import json
from decimal import Decimal
from pathlib import Path
import pytest
from market_data.collectors.vast import parse_response
from market_data.errors import SchemaError

FIXTURES=Path(__file__).parents[1]/"fixtures"/"vast"

def test_vast_contract_fixtures_parse():
    for name,contract in (("ondemand.json","on-demand"),("bid.json","interruptible"),("reserved.json","reserved")):
        rows=parse_response((FIXTURES/name).read_bytes(),contract)
        assert rows and rows[0]["gpu_name"]

def test_vast_rejects_truncated_and_empty():
    with pytest.raises(SchemaError,match="truncated"):
        parse_response(b'{"offers":[],"truncated":true}',"on-demand")
    with pytest.raises(SchemaError,match="no offers"):
        parse_response(b'{"offers":[],"truncated":false}',"on-demand")
