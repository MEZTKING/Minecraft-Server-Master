"""Opening the server files the user downloaded.

Hosts such as PebbleHost let you download the server either as a folder
(via SFTP / the file manager) or as a ``.zip`` backup. :func:`open_server`
accepts both and returns the directory that actually contains the server
(the one with ``server.properties`` or ``logs/``), because zips often wrap
everything in an extra top-level folder.

Zip backups are read *selectively* -- see "lite extraction" below.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
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


def _is_safe(dest_real: str, name: str) -> bool:
    target = os.path.realpath(os.path.join(dest_real, name))
    return target == dest_real or target.startswith(dest_real + os.sep)


def _restore_mtime(target: str, info: zipfile.ZipInfo) -> None:
    """zipfile doesn't restore modification times; latest.log is dated by its mtime."""
    try:
        ts = time.mktime(info.date_time + (0, 0, -1))
        os.utime(target, (ts, ts))
    except (OverflowError, ValueError, OSError):
        pass


# --- selective ("lite") extraction ---------------------------------------------
#
# Real backups are huge (a 26.1 world was 14.7 GB), almost all of it region data that
# the parsers never read. Lite extraction skips that, and replaces every replay archive
# with a small *stub* holding only its metadata and the first part of its first chunk
# (enough for start times). ensure_full() extracts a real replay on demand.

_HEAVY_DIRS = {"region", "entities", "poi", "DistantHorizons", "distant_horizons_server_data",
               "libraries", "versions", ".fabric", "cache", "bluemap", "dynmap"}
_HEAVY_EXT = (".mca", ".mcc", ".sqlite", ".sqlite-wal", ".sqlite-shm", ".db")
_REPLAY_AREA = ("record", "replay", "flashback")
_STUB_HEAD_BYTES = 1 << 20          # first MiB of c0.flashback / recording.tmcpr
STUB_MARKER = "mcsm_stub.json"

# root -> {"zip": path, "prefix": str, "stubs": set(rel paths)}
_SOURCES: dict[str, dict] = {}


def _is_heavy(name: str, include_regions: bool) -> bool:
    parts = name.split("/")
    if include_regions and "region" in parts and name.endswith(".mca"):
        return False
    if any(p in _HEAVY_DIRS for p in parts[:-1]):
        return True
    return name.lower().endswith(_HEAVY_EXT)


def _is_replay_archive(name: str) -> bool:
    low = name.lower()
    if low.endswith(".mcpr"):
        return True
    return low.endswith(".zip") and any(k in low for k in _REPLAY_AREA)


def _write_stub(zf: zipfile.ZipFile, info: zipfile.ZipInfo, target: str) -> bool:
    """Write a small stand-in for a replay archive. False if it isn't a readable zip."""
    try:
        with zf.open(info) as raw:
            inner = zipfile.ZipFile(raw)
            names = inner.namelist()
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as out:
                for n in names:
                    base = n.rsplit("/", 1)[-1]
                    if n.endswith(".json") or base in ("icon.png",):
                        out.writestr(n, inner.read(n))
                    elif base in ("c0.flashback", "recording.tmcpr"):
                        with inner.open(n) as fh:
                            out.writestr(n, fh.read(_STUB_HEAD_BYTES))
                out.writestr(STUB_MARKER, json.dumps({"original_size": info.file_size, "member": info.filename}))
        _restore_mtime(target, info)
        return True
    except (zipfile.BadZipFile, OSError, KeyError, EOFError):
        return False


def _extract_lite(path: str, dest: str, include_regions: bool) -> dict:
    dest_real = os.path.realpath(dest)
    stubs = set()
    listing: dict[str, int] = {}          # every file in the zip -> uncompressed size
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            name = info.filename
            if info.is_dir() or not _is_safe(dest_real, name):
                continue
            listing[name] = info.file_size
            if _is_heavy(name, include_regions):
                continue
            target = os.path.join(dest, name)
            if _is_replay_archive(name) and _write_stub(zf, info, target):
                stubs.add(name)
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out, 1 << 20)
            _restore_mtime(target, info)
    return {"zip": os.path.abspath(path), "dest": dest, "stubs": stubs, "listing": listing}


def zip_listing(folder: str) -> dict[str, int] | None:
    """For a folder inside a selectively-extracted backup: ``{relative path: size}`` of
    *everything* the backup zip holds below it (including skipped region files).
    None for ordinary folders."""
    real = os.path.realpath(folder)
    for info in _SOURCES.values():
        dest = os.path.realpath(info["dest"])
        if real == dest or real.startswith(dest + os.sep):
            prefix = os.path.relpath(real, dest).replace(os.sep, "/")
            prefix = "" if prefix == "." else prefix + "/"
            return {n[len(prefix):]: sz for n, sz in info["listing"].items() if n.startswith(prefix)}
    return None


def is_stub(path: str) -> bool:
    try:
        with zipfile.ZipFile(path) as zf:
            return STUB_MARKER in zf.namelist()
    except (zipfile.BadZipFile, OSError):
        return False


def stub_original_size(path: str) -> int | None:
    try:
        with zipfile.ZipFile(path) as zf:
            return int(json.loads(zf.read(STUB_MARKER))["original_size"])
    except (zipfile.BadZipFile, OSError, KeyError, ValueError):
        return None


def ensure_full(path: str) -> str:
    """Make sure *path* (a replay inside an opened server) is the complete file.

    If it is a lite-extraction stub, the real archive is extracted over it from the
    original backup zip. Returns *path*.
    """
    if not is_stub(path):
        return path
    real = os.path.realpath(path)
    for info in _SOURCES.values():
        dest = os.path.realpath(info["dest"])
        if real.startswith(dest + os.sep):
            member = os.path.relpath(real, dest).replace(os.sep, "/")
            with zipfile.ZipFile(info["zip"]) as zf:
                zi = zf.getinfo(member)
                with zf.open(zi) as src, open(path + ".part", "wb") as out:
                    shutil.copyfileobj(src, out, 1 << 20)
            os.replace(path + ".part", path)
            _restore_mtime(path, zi)
            return path
    raise FileNotFoundError(f"cannot find the backup that {path} came from")


@contextmanager
def open_server(path: str, include_regions: bool = False) -> Iterator[str]:
    """Context manager yielding the server root directory for *path*.

    *path* may be a directory (used in place) or a ``.zip`` backup. Zips are extracted
    *selectively* into a temporary directory that is deleted afterwards: region files
    are skipped unless *include_regions* (the live map needs them), and replays become
    small stubs (see :func:`ensure_full`).
    """
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    if os.path.isdir(path):
        yield find_server_root(path)
        return
    if zipfile.is_zipfile(path):
        tmp = tempfile.mkdtemp(prefix="mcsm-")
        try:
            _SOURCES[tmp] = _extract_lite(path, tmp, include_regions)
            yield find_server_root(tmp)
        finally:
            _SOURCES.pop(tmp, None)
            shutil.rmtree(tmp, ignore_errors=True)
        return
    raise ValueError(f"{path} is neither a directory nor a zip file")
