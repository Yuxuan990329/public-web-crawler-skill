import os
from pathlib import Path


def reserve_output_paths(paths):
    reserved = []
    try:
        for value in paths:
            path = Path(value).expanduser().resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(descriptor)
            reserved.append(path)
    except Exception:
        for path in reserved:
            path.unlink(missing_ok=True)
        raise
    return reserved
