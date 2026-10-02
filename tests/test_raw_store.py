import gzip
from market_data.raw_store import quarantine, store_raw


def test_raw_file_is_reproducible_and_quarantinable(tmp_path):
    body = b'{"answer":42}'
    item = store_raw(tmp_path, "source", "2025-01-02T03:04:05Z", "run", body)
    assert gzip.decompress(item.path.read_bytes()) == body
    assert item.sha256 == "ecf59a2696ca44a417e20e2a7eabb1b26e82c779f8546bea354a2cc80e8e1eed"
    assert quarantine(tmp_path, "source", item.path).exists()
