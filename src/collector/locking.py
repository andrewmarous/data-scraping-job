from contextlib import contextmanager
import fcntl
from pathlib import Path


class AlreadyRunning(RuntimeError):
    pass


@contextmanager
def lock(data_dir: Path):
    data_dir.mkdir(parents=True, exist_ok=True)
    # Never unlink the file: another process can hold its inode open.
    with (data_dir / "collector.lock").open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise AlreadyRunning("Collection is already running") from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)
