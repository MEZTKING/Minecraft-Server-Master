"""Opening the server files the user downloaded.

Hosts such as PebbleHost let you download the server either as a folder
(via SFTP / the file manager) or as a ``.zip`` backup. :func:`open_server`
accepts both and returns the directory that actually contains the server
(the one with ``server.properties`` or ``logs/``), because zips often wrap
everything in an extra top-level folder.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import zipfile
from contextlib import contextmanager
from typing import Iterator

# Files/folders whose presence marks a server root, strongest signals first.
_ROOT_MARKERS = ("server.properties", "usercache.json", "logs", "eula.txt")


def find_server_root(path: str, max_depth: int = 3) -> str:
    """Return the directory under *path* that looks like a server root."""
    path = os.path.abspath(path)

    def score(d: str) -> int:
        return sum(1 for m in _ROOT_MARKERS if os.path.exists(os.path.join(d, m)))

    best, best_score = path, score(path)
    if best_score >= 2:
        return path
    for current, dirs, _files in os.walk(path):
        depth = os.path.relpath(current, path).count(os.sep) + (current != path)
        if depth > max_depth:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        s = score(current)
        if s > best_score:
            best, best_score = current, s
    return best


def _safe_extract(zf: zipfile.ZipFile, dest: str) -> None:
    """Extract a zip, refusing entries that would escape *dest* ("zip slip")."""
    dest_real = os.path.realpath(dest)
    for member in zf.infolist():
        target = os.path.realpath(os.path.join(dest, member.filename))
        if not (target == dest_real or target.startswith(dest_real + os.sep)):
            raise ValueError(f"refusing unsafe path in zip: {member.filename}")
    zf.extractall(dest)
    # Restore modification times: zipfile does not, and the log parser can
    # use latest.log's mtime to date it.
    for member in zf.infolist():
        if member.is_dir():
            continue
        target = os.path.join(dest, member.filename)
        try:
            import time
            ts = time.mktime(member.date_time + (0, 0, -1))
            os.utime(target, (ts, ts))
        except (OverflowError, ValueError, OSError):
            pass


@contextmanager
def open_server(path: str) -> Iterator[str]:
    """Context manager yielding the server root directory for *path*.

    *path* may be a directory or a ``.zip`` file. Zips are extracted into a
    temporary directory that is deleted afterwards.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    if os.path.isdir(path):
        yield find_server_root(path)
        return
    if zipfile.is_zipfile(path):
        tmp = tempfile.mkdtemp(prefix="mcsm-")
        try:
            with zipfile.ZipFile(path) as zf:
                _safe_extract(zf, tmp)
            yield find_server_root(tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return
    raise ValueError(f"{path} is neither a directory nor a zip file")
