"""Loading a server, a set of backups or a library world, remembered between runs.

Reading a big library world (every backup's logs and stats, every recording) takes a while. The result is
saved in ``%LOCALAPPDATA%/mcsm/loaded/`` and used again next time, **unless something changed**: the key
is a fingerprint of the files that loading reads (names, sizes and change times; world region files are
left out, the map reads those itself), the options (time zone, latest.log date) and the app's version
(a newer app may read more). Only folders are remembered (library worlds and unpacked servers): a .zip is
unpacked into a temporary folder each time, which the result would point into.

The ignore list (:mod:`mcsm.ignore`) is applied after loading, so changing it needs no reload.
"""

from __future__ import annotations

import hashlib
import os
import pickle
from contextlib import ExitStack
from datetime import date, tzinfo

from . import models as m
from . import progress as jobs

CACHE_VERSION = 3                # 3: statistic groups per backup (year in review); 2: celebrations
_SKIP_DIRS = {"region", "entities", "poi", ".cache", "__pycache__"}


def cache_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "mcsm", "loaded")


def fingerprint(paths: list[str]) -> str | None:
    """A hash of every file loading reads under *paths*; None when one of them can't be remembered
    (a zip, or missing)."""
    h = hashlib.sha1()
    n = 0
    for top in paths:
        if not os.path.isdir(top):
            return None
        h.update(f"#{os.path.abspath(top)}\n".encode())
        for cur, dirs, files in os.walk(top):
            dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS)
            for f in sorted(files):
                full = os.path.join(cur, f)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                h.update(f"{os.path.relpath(full, top)}|{st.st_size}|{st.st_mtime_ns}\n".encode())
                n += 1
                if n % 500 == 0:
                    jobs.report(None, None, f"Checking for changes: {n:,} files")
    return h.hexdigest()


def _key(paths: list[str], tz_spec: str, latest: date | None) -> str | None:
    from . import version_string
    fp = fingerprint(paths)
    if fp is None:
        return None
    return hashlib.sha1(f"{CACHE_VERSION}|{version_string()}|{tz_spec}|{latest}|{fp}".encode()).hexdigest()[:24]


def load(paths: list[str], stack: ExitStack, tz: tzinfo | None = None, tz_spec: str = "",
         latest: date | None = None, say=print, use_cache: bool = True, cache: str | None = None) -> m.ServerData:
    """Load *paths* like the app's Load button (a library world = all its backups and recordings),
    from the saved result when nothing changed. Returns the data with ``loaded_from_cache`` set."""
    from .analyzer import analyze
    from .archive import analyze_many
    from .library import expand_sources
    from .source import open_server
    servers, recs, world = expand_sources(paths)
    key = _key(servers + recs, tz_spec, latest) if use_cache else None
    file = os.path.join(cache or cache_dir(), key + ".pickle") if key else None
    if file and os.path.isfile(file):
        jobs.report(None, None, "Opening the saved result (nothing changed since last time)")
        try:
            with open(file, "rb") as fh:
                data = pickle.load(fh)
            if isinstance(data, m.ServerData):
                for s in servers:                      # folders are used in place: nothing to unpack
                    stack.enter_context(open_server(s))
                data.loaded_from_cache = True
                say(f"Opened the saved result of the last load ({len(data.replays)} recordings): nothing changed.")
                return data
        except Exception:                              # noqa: BLE001 -- a bad saved file: just load again
            pass
    if len(servers) == 1 and not recs:
        root = stack.enter_context(open_server(servers[0]))
        say(f"Reading {servers[0]}")
        data = analyze(root, tz=tz, latest_log_date=latest)
    else:
        data = analyze_many(servers, stack, tz=tz, latest_log_date=latest, progress=say, recordings=recs)
    data.library_world = world
    if file:
        try:
            os.makedirs(os.path.dirname(file), exist_ok=True)
            with open(file + ".part", "wb") as fh:
                pickle.dump(data, fh, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(file + ".part", file)
            _prune(os.path.dirname(file), keep=file)
        except Exception:                              # noqa: BLE001 -- saving is a bonus
            pass
    data.loaded_from_cache = False
    return data


def _prune(folder: str, keep: str, most: int = 4) -> None:
    """Keep the few most recent saved results (each big world is tens of MB)."""
    files = sorted((os.path.join(folder, f) for f in os.listdir(folder) if f.endswith(".pickle")),
                   key=os.path.getmtime, reverse=True)
    for f in files[most:]:
        if f != keep:
            try:
                os.remove(f)
            except OSError:
                pass
