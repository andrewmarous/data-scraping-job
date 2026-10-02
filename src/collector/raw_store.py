import gzip
import hashlib
import os
from pathlib import Path
import tempfile


def store(data_dir: Path, retrieval_id: str, body: bytes) -> tuple[str, str]:
    directory = data_dir / "raw"
    directory.mkdir(parents=True, exist_ok=True)
    relative = f"raw/{retrieval_id}.bin.gz"
    fd, temporary = tempfile.mkstemp(dir=directory, prefix=".raw-")
    try:
        with os.fdopen(fd, "wb") as stream:
            with gzip.GzipFile(fileobj=stream, mode="wb", mtime=0) as compressed:
                compressed.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        # No overwrite, even if an identifier is accidentally reused.
        os.link(temporary, data_dir / relative)
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        os.unlink(temporary)
    return relative, hashlib.sha256(body).hexdigest()


def read(data_dir: Path, row, limit: int) -> bytes:
    path = (data_dir / row["path"]).resolve()
    if not path.is_relative_to(data_dir.resolve()):
        raise ValueError("Invalid raw path")
    with gzip.open(path, "rb") as stream:
        body = stream.read(limit + 1)
    if len(body) > limit or hashlib.sha256(body).hexdigest() != row["sha256"]:
        raise ValueError("Raw size or checksum mismatch")
    return body
