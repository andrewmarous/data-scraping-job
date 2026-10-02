import json, os, socket, time
import pytest
from market_data.errors import LockActive
from market_data.locking import SourceLock


def test_active_lock_skips(tmp_path):
    path = tmp_path / "x.lock"
    path.write_text(json.dumps({"pid":os.getpid(),"hostname":socket.gethostname(),"started":time.time()}))
    with pytest.raises(LockActive):
        with SourceLock(path, 60): pass

def test_stale_lock_is_reclaimed(tmp_path):
    path = tmp_path / "x.lock"
    path.write_text(json.dumps({"pid":999999999,"hostname":socket.gethostname(),"started":0}))
    with SourceLock(path, 60): assert path.exists()
    assert not path.exists()
