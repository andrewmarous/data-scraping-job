import json

from typer.testing import CliRunner

from market_data.cli import app
from market_data.index_verification import CANDIDATES, eligible_sources


def test_no_release_4_candidate_passes_all_gates():
    assert eligible_sources() == frozenset()
    assert CANDIDATES["computeprices"].republication_verified is False
    assert CANDIDATES["gpu_ai"].machine_readable_endpoint is False


def test_index_status_is_machine_readable():
    result = CliRunner().invoke(app, ["indexes", "status", "--json"])
    assert result.exit_code == 0
    rows = json.loads(result.stdout)
    assert {row["source"] for row in rows} == {"computeprices", "gpu_ai"}
    assert all(row["eligible"] is False for row in rows)
