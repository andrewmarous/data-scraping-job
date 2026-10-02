import json

import pytest

from market_data.collectors.provider_catalog import parse_response, _normalized
from market_data.errors import SchemaError


def body(value): return json.dumps(value).encode()


def test_parses_live_marketplace_shapes():
    assert parse_response("clore", body({"servers":[{"id":1}]}))[0]["id"] == 1
    assert parse_response("gcore", body({"results":[{"name":"h100-spot","capacity":2}]}))[0]["capacity"] == 2
    assert parse_response("saladcloud", body({"items":[{"id":"x","name":"RTX 4090"}]}))[0]["name"] == "RTX 4090"
    assert parse_response("runpod", body({"data":{"items":[{"id":"gpu"}]}}))[0]["id"] == "gpu"


@pytest.mark.parametrize("count", [1, 2, 4, 8])
def test_runpod_shape_queries_preserve_region_and_unknown_capacity(count):
    rows = parse_response("runpod", body({"gpus": [{
        "id": "NVIDIA B300 SXM6 AC", "secure": True, "community": False,
        "maxCount": {"secure": 8, "community": 0}, "availability": "LOW",
        "price": {"secure": 7.89}, "dataCenters": [{"id": "US-WA-2", "availability": "LOW"}]
    }]}), shape_count=count)
    assert len(rows) == 1
    fields = _normalized("runpod", rows[0], None)
    assert fields[1:3] == ("product_catalog", "on-demand")
    assert fields[5] == count and fields[7] == "US-WA-2" and fields[9] is None
    assert f":{count}:US-WA-2" in fields[0]


def test_lambda_capacity_is_not_inferred_from_empty_regions():
    spec = {"gpu_description": "B200", "price_cents_per_hour": 679,
            "specs": {"gpus": 8}}
    rows = parse_response("lambda", body({"data": {
        "gpu_8x_b200": {"instance_type": spec, "regions_with_capacity_available": [{"name": "us-west-1"}]},
        "gpu_1x_b200": {"instance_type": {**spec, "specs": {"gpus": 1}}, "regions_with_capacity_available": []},
    }}))
    live, missing = [_normalized("lambda", row, None) for row in rows]
    assert live[1:3] == ("live_capacity", "on-demand")
    assert live[5] == 8 and live[7] == "us-west-1" and live[9] is None
    assert live[10:13] == ("6.79", "USD", "instance hour")
    assert missing[1] == "product_catalog" and missing[5] == 1 and missing[9] is None
    with pytest.raises(SchemaError):
        parse_response("lambda", body({"data": {"bad": {"instance_type": spec}}}))


def test_filters_hyperscaler_gpu_spot_catalogs():
    azure={"Items":[{"meterId":"1","skuName":"NC H100 Spot","armSkuName":"Standard_NC_H100"},{"meterId":"2","skuName":"D4 Spot","armSkuName":"Standard_D4"}]}
    assert [r["meterId"] for r in parse_response("azure",body(azure))] == ["1"]
    google={"skus":[{"name":"gpu","description":"Nvidia H100 GPU Spot Preemptible"},{"name":"cpu","description":"CPU Spot"}]}
    assert [r["name"] for r in parse_response("google_cloud",body(google))] == ["gpu"]


def test_rejects_schema_drift_and_accepts_pricing_document():
    with pytest.raises(SchemaError): parse_response("clore",body({"unexpected":[]}))
    assert parse_response("coreweave",("spot pricing "+"x"*100).encode())[0]["source_key"] == "pricing-document"
