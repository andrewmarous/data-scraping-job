import json
import subprocess
from market_data.collectors.aws_spot import discover_regions, parse_prices

def test_discovers_all_enabled_regions():
    body={"Regions":[
        {"RegionName":"us-east-1","OptInStatus":"opt-in-not-required"},
        {"RegionName":"af-south-1","OptInStatus":"opted-in"},
        {"RegionName":"ap-east-1","OptInStatus":"not-opted-in"}]}
    def runner(command,**kwargs):
        return subprocess.CompletedProcess(command,0,json.dumps(body).encode(),b"")
    assert discover_regions("profile",runner)==["af-south-1","us-east-1"]

def test_parses_price_page_and_token():
    body=json.dumps({"SpotPriceHistory":[{"Timestamp":"2026-08-31T00:00:00Z","AvailabilityZone":"us-east-1a",
        "InstanceType":"g5.xlarge","ProductDescription":"Linux/UNIX","SpotPrice":"0.4200"}],"NextToken":"next"}).encode()
    rows,token=parse_prices(body,"us-east-1")
    assert rows[0]["price"]=="0.4200"
    assert rows[0]["timestamp"]=="2026-08-31T00:00:00Z"
    assert token=="next"
