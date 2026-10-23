"""Small filesystem helpers used by generated data and run artifacts."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    fcntl = None


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def write_json_atomic(
    path: Path,
    payload: Any,
    *,
    backup_path: Path | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        if backup_path is not None and path.exists():
            backup_fd, backup_tmp = tempfile.mkstemp(
                dir=backup_path.parent,
                prefix=f".{backup_path.name}.",
                suffix=".tmp",
            )
            os.close(backup_fd)
            try:
                shutil.copyfile(path, backup_tmp)
                with open(backup_tmp, "rb+") as handle:
                    os.fsync(handle.fileno())
                os.replace(backup_tmp, backup_path)
            finally:
                if os.path.exists(backup_tmp):
                    os.unlink(backup_tmp)
        os.replace(tmp_name, path)
        _fsync_directory(path.parent)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


@contextlib.contextmanager
def exclusive_file_lock(path: Path) -> Iterator[None]:
    """Serialize store writers across processes; no-op where fcntl is absent."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+", encoding="utf-8") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
