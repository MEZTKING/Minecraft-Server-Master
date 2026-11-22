"""One library folder for all of a server's backups and recordings.

Backups pile up in many places: zips from the host, ``.tgz`` files, extracted folders. Each
contains the same recordings again. The library keeps them in one tidy place::

    mcsm-library/
      mcsm-library.json
      <World>/                          one folder per world (level name + seed)
        world.json
        backups/2026-05-19 21-04/       server files, without recordings
          mcsm-backup.json              where it came from, when, version, counts
        recordings/<path in server>     every backup's recordings, each stored once
        recordings/imported/<name>      recordings added by hand (e.g. Flashback client replays)
        highlights.json                 history-viewer highlights for this world

Importing never changes the source in ``link`` or ``copy`` mode. ``link`` (the default when
source and library are on the same drive) uses hard links: instant, no extra space, and the
original can be deleted afterwards without losing anything. A file identical to the same file
in another backup of the world is hard-linked to it instead of stored twice.

Any command accepts a library world folder as its server: :func:`expand_sources` turns it
into that world's backups plus its recordings folder.
"""

from __future__ import annotations

import filecmp
import json
import os
import re
import shutil
import tarfile
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from typing import Callable

from . import world as world_mod
from .replays import _is_flashback_dir, _is_replaymod_dir, find_replay_sources
from .source import _is_safe, _restore_mtime, find_server_root

LIBRARY_MARKER = "mcsm-library.json"
WORLD_MARKER = "world.json"
BACKUP_MARKER = "mcsm-backup.json"
MODES = ("auto", "link", "copy", "move")


def default_library_dir() -> str:
    home = os.path.expanduser("~")
    docs = os.path.join(home, "Documents")
    return os.path.join(docs if os.path.isdir(docs) else home, "mcsm-library")


def _safe(text: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", str(text)).strip(" .") or "world"


def _read_json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            v = json.load(fh)
        return v if isinstance(v, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: str, obj: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, default=str)
    os.replace(tmp, path)


def is_library(path: str) -> bool:
    return os.path.isfile(os.path.join(path, LIBRARY_MARKER))


def is_world_dir(path: str) -> bool:
    return os.path.isfile(os.path.join(path, WORLD_MARKER)) and os.path.isdir(os.path.join(path, "backups"))


def ensure_library(library: str) -> str:
    library = os.path.abspath(library)
    os.makedirs(library, exist_ok=True)
    marker = os.path.join(library, LIBRARY_MARKER)
    if not os.path.isfile(marker):
        _write_json(marker, {"format": 1, "created": datetime.now(timezone.utc).isoformat()})
    return library


# --- reading a library ---------------------------------------------------------------

def world_backups(world_dir: str) -> list[dict]:
    """The backups of a library world, oldest first: ``[{path, name, **mcsm-backup.json}]``."""
    out = []
    bdir = os.path.join(world_dir, "backups")
    for name in sorted(os.listdir(bdir)) if os.path.isdir(bdir) else []:
        p = os.path.join(bdir, name)
        if os.path.isdir(p):
            out.append({**_read_json(os.path.join(p, BACKUP_MARKER)), "name": name, "path": p})
    out.sort(key=lambda b: (b.get("snapshot_ms") or 0, b["name"]))
    return out


def world_recordings(world_dir: str) -> list[str]:
    rec = os.path.join(world_dir, "recordings")
    return [p for _k, p in find_replay_sources(rec)] if os.path.isdir(rec) else []


def list_worlds(library: str) -> list[dict]:
    out = []
    for name in sorted(os.listdir(library)) if os.path.isdir(library) else []:
        p = os.path.join(library, name)
        if is_world_dir(p):
            out.append({**_read_json(os.path.join(p, WORLD_MARKER)), "name": name, "path": p,
                        "backups": world_backups(p), "recordings": len(world_recordings(p))})
    return out


def expand_sources(paths: list[str]) -> tuple[list[str], list[str], str | None]:
    """Turn command-line SERVER paths into ``(servers, recordings_dirs, world_dir)``.

    A library world folder becomes all its backups plus its recordings folder. A library
    folder with a single world counts as that world.
    """
    servers, recs, world = [], [], None
    for p in paths:
        w = p
        if is_library(p) and not is_world_dir(p):
            worlds = list_worlds(p)
            if len(worlds) != 1:
                names = ", ".join(x["name"] for x in worlds) or "none yet"
                raise ValueError(f"{p} is a library with {len(worlds)} worlds ({names}): give one world's folder")
            w = worlds[0]["path"]
        if is_world_dir(w):
            bs = world_backups(w)
            if not bs:
                raise ValueError(f"the library world {w} has no backups yet")
            servers += [b["path"] for b in bs]
            rec = os.path.join(w, "recordings")
            if os.path.isdir(rec):
                recs.append(rec)
            world = world or os.path.abspath(w)
        else:
            servers.append(p)
    return servers, recs, world


# --- importing -----------------------------------------------------------------------

def _probe(root: str) -> dict:
    """Level name, seed, version and snapshot time of an (extracted) server, cheaply."""
    props = world_mod.read_properties(os.path.join(root, "server.properties"))
    wd = world_mod.world_dir(root, props)
    lvl = world_mod.read_level_dat(wd) if os.path.isdir(wd) else {}
    times = []
    lp = lvl.get("last_played_ms")
    if isinstance(lp, (int, float)) and lp > 0:
        times.append(int(lp))
    latest = os.path.join(root, "logs", "latest.log")
    if os.path.isfile(latest):
        times.append(int(os.path.getmtime(latest) * 1000))
    return {"level_name": lvl.get("level_name") or props.get("level-name") or "world",
            "seed": lvl.get("seed"), "version": lvl.get("version_name"),
            "snapshot_ms": max(times) if times else None}


def _find_or_make_world(library: str, info: dict, name: str | None) -> str:
    level, seed = info["level_name"], info["seed"]
    if name:
        w = os.path.join(library, _safe(name))
    else:
        w = None
        for x in list_worlds(library):
            if x.get("level_name") == level and (seed is None or x.get("seed") is None or x.get("seed") == seed):
                w = x["path"]
                break
        if w is None:
            base, n = _safe(level), 2
            w = os.path.join(library, base)
            while os.path.exists(w):
                w, n = os.path.join(library, f"{base} ({n})"), n + 1
    os.makedirs(os.path.join(w, "backups"), exist_ok=True)
    marker = os.path.join(w, WORLD_MARKER)
    meta = _read_json(marker)
    if not meta or (meta.get("seed") is None and seed is not None):
        _write_json(marker, {"level_name": meta.get("level_name") or level, "seed": seed,
                             "created": meta.get("created") or datetime.now(timezone.utc).isoformat()})
    return w


def _same_drive(a: str, b: str) -> bool:
    try:
        return os.stat(a).st_dev == os.stat(b).st_dev
    except OSError:
        return False


def _place(src: str, dst: str, mode: str, stats: dict) -> None:
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    size = os.path.getsize(src)
    if mode == "link":
        try:
            os.link(src, dst)
            stats["linked"] += 1
            return
        except OSError:
            pass                     # other drive, FAT32, link limit: copy instead
    if mode == "move":
        try:
            os.replace(src, dst)                       # same drive: instant
        except OSError:
            shutil.move(src, dst)                      # other drive: copy + delete
            stats["bytes_written"] += size
        stats["moved"] += 1
        return
    shutil.copy2(src, dst)
    stats["copied"] += 1
    stats["bytes_written"] += size


def _identical(a: str, b: str) -> bool:
    try:
        sa, sb = os.stat(a), os.stat(b)
    except OSError:
        return False
    if sa.st_size != sb.st_size:
        return False
    if os.path.samefile(a, b):
        return True
    return filecmp.cmp(a, b, shallow=False)


def _recording_index(rec_root: str) -> dict[tuple[str, int], list[str]]:
    """(file name, size) -> paths of the recording files already in a world."""
    index: dict[tuple[str, int], list[str]] = {}
    if os.path.isdir(rec_root):
        for p in _walk_files(rec_root):
            index.setdefault((os.path.basename(p).lower(), os.path.getsize(p)), []).append(p)
    return index


def _find_twin(index: dict, path: str, size: int) -> str | None:
    return next((c for c in index.get((os.path.basename(path).lower(), size), []) if _identical(path, c)), None)


def _link(existing: str, target: str) -> bool:
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        os.link(existing, target)
        return True
    except OSError:
        return False


def _replay_paths(root: str) -> list[str]:
    """Replay files/folders in a server, relative to it ('/'-separated)."""
    return [os.path.relpath(p, root).replace(os.sep, "/") for _k, p in find_replay_sources(root)]


def _walk_files(top: str):
    for cur, dirs, files in os.walk(top):
        dirs.sort()
        for f in sorted(files):
            yield os.path.join(cur, f)


def _import_root(root: str, library: str, mode: str, world_name: str | None, source: str,
                 say: Callable[[str], None], force: bool = False) -> dict:
    info = _probe(root)
    w = _find_or_make_world(library, info, world_name)
    when = info["snapshot_ms"]
    stamp = datetime.fromtimestamp(when / 1000).strftime("%Y-%m-%d %H-%M") if when else "undated"
    for b in world_backups(w):
        if b.get("snapshot_ms") == when and when and not force:
            say(f"Already in the library: {b['path']}")
            return {"world": w, "backup": b["path"], "skipped": True, **info}
    dest, n = os.path.join(w, "backups", stamp), 2
    while os.path.exists(dest):
        dest, n = os.path.join(w, "backups", f"{stamp} ({n})"), n + 1
    if mode == "auto":
        mode = "link" if _same_drive(root, w) else "copy"

    # Index of the world's existing files for de-duplication: (rel, size) -> path.
    have: dict[tuple[str, int], list[str]] = {}
    for b in world_backups(w):
        for p in _walk_files(b["path"]):
            rel = os.path.relpath(p, b["path"]).replace(os.sep, "/")
            if rel != BACKUP_MARKER:
                have.setdefault((rel, os.path.getsize(p)), []).append(p)

    stats = {"files": 0, "linked": 0, "copied": 0, "moved": 0, "deduplicated": 0, "bytes_written": 0,
             "recordings_new": 0, "recordings_known": 0, "bytes": 0}
    replays = _replay_paths(root)
    rec_root = os.path.join(w, "recordings")
    replay_set = set(replays)

    def in_replay(rel: str) -> str | None:
        parts = rel.split("/")
        for i in range(1, len(parts) + 1):
            head = "/".join(parts[:i])
            if head in replay_set:
                return head
        return None
    seen_replays: dict[str, bool] = {}
    rec_index = _recording_index(rec_root)
    t0, last = time.monotonic(), 0.0
    for p in _walk_files(root):
        rel = os.path.relpath(p, root).replace(os.sep, "/")
        if rel in (BACKUP_MARKER, "mcsm_stub.json"):
            continue
        size = os.path.getsize(p)
        stats["files"] += 1
        stats["bytes"] += size
        r = in_replay(rel)
        if r is not None:                                   # a recording: store once per world
            target = os.path.join(rec_root, *rel.split("/"))
            if os.path.exists(target):
                if _identical(p, target):
                    if r not in seen_replays:
                        stats["recordings_known"] += 1
                    seen_replays[r] = False
                    if mode == "move":
                        os.remove(p)
                    continue
                stem, ext = os.path.splitext(target)
                k = 2
                while os.path.exists(f"{stem} ({k}){ext}"):
                    k += 1
                target = f"{stem} ({k}){ext}"
            twin = _find_twin(rec_index, p, size)
            if twin and r == rel:                       # same recording stored under another folder name
                if r not in seen_replays:
                    stats["recordings_known"] += 1
                seen_replays[r] = False
                if mode == "move":
                    os.remove(p)
                continue
            if r not in seen_replays:
                stats["recordings_new"] += 1
            seen_replays[r] = True
            if twin and _link(twin, target):
                stats["deduplicated"] += 1
                if mode == "move":
                    os.remove(p)
            else:
                _place(p, target, mode, stats)
            rec_index.setdefault((os.path.basename(target).lower(), size), []).append(target)
        else:
            target = os.path.join(dest, *rel.split("/"))
            twin = next((c for c in have.get((rel, size), []) if _identical(p, c)), None)
            if twin:
                try:
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    os.link(twin, target)
                    stats["deduplicated"] += 1
                    if mode == "move":
                        os.remove(p)
                    continue
                except OSError:
                    pass
            _place(p, target, mode, stats)
        now = time.monotonic()
        if now - last > 2:
            last = now
            say(f"  {stats['files']:,} files, {stats['bytes'] / 1e9:.1f} GB ({now - t0:.0f} s)")
    os.makedirs(dest, exist_ok=True)
    meta = {"source": os.path.abspath(source), "imported": datetime.now(timezone.utc).isoformat(),
            "mode": mode, "snapshot_ms": when, "level_name": info["level_name"], "seed": info["seed"],
            "version": info["version"], **stats}
    _write_json(os.path.join(dest, BACKUP_MARKER), meta)
    return {"world": w, "backup": dest, "skipped": False, **meta}


def _is_tar(path: str) -> bool:
    try:
        return tarfile.is_tarfile(path)
    except OSError:
        return False


class _Progress:
    """File wrapper reporting how far through a big archive the reading is."""

    def __init__(self, fh, total: int, say: Callable[[str], None]):
        self.fh, self.total, self.say, self.done, self.last, self.t0 = fh, total, say, 0, 0.0, time.monotonic()

    def read(self, n=-1):
        b = self.fh.read(n)
        self.done += len(b)
        now = time.monotonic()
        if now - self.last > 3:
            self.last = now
            pct = 100 * self.done / self.total if self.total else 0
            self.say(f"  unpacking {pct:.0f}% ({self.done / 1e9:.1f} of {self.total / 1e9:.1f} GB, "
                     f"{now - self.t0:.0f} s)")
        return b

    def __getattr__(self, name):
        return getattr(self.fh, name)


def _local_name(name: str) -> str:
    """An archive member name that Windows can create: ``Recordings for: X`` -> ``Recordings for_ X``
    (what Windows unpacking tools do too, and what the replay reader expects)."""
    if os.name != "nt":
        return name
    bad = '[<>:"|?*' + "".join(chr(c) for c in range(32)) + "]"
    parts = [re.sub(bad, "_", p).rstrip(" .") or "_" for p in name.replace(chr(92), "/").split("/")]
    return "/".join(parts)


def _extract_tar(path: str, dest: str, say: Callable[[str], None]) -> None:
    dest_real = os.path.realpath(dest)
    with open(path, "rb") as raw:
        wrapped = _Progress(raw, os.path.getsize(path), say)
        with tarfile.open(fileobj=wrapped, mode="r|*") as tf:
            for member in tf:
                name = _local_name(member.name)
                if not (member.isfile() or member.isdir()) or not _is_safe(dest_real, name):
                    continue                    # links, devices, paths escaping dest: never extracted
                target = os.path.join(dest, name)
                if member.isdir():
                    os.makedirs(target, exist_ok=True)
                    continue
                os.makedirs(os.path.dirname(target), exist_ok=True)
                src = tf.extractfile(member)
                if src is None:
                    continue
                with src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out, 1 << 20)
                try:
                    os.utime(target, (member.mtime, member.mtime))
                except (OSError, OverflowError, ValueError):
                    pass


def _extract_zip(path: str, dest: str, say: Callable[[str], None]) -> None:
    dest_real = os.path.realpath(dest)
    with zipfile.ZipFile(path) as zf:
        infos = zf.infolist()
        total = sum(i.file_size for i in infos) or 1
        done, last = 0, 0.0
        for info in infos:
            name = _local_name(info.filename)
            if info.is_dir() or not _is_safe(dest_real, name):
                continue
            target = os.path.join(dest, name)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out, 1 << 20)
            _restore_mtime(target, info)
            done += info.file_size
            if time.monotonic() - last > 3:
                last = time.monotonic()
                say(f"  unpacking {100 * done / total:.0f}%")


def import_backup(source: str, library: str | None = None, mode: str = "auto", world: str | None = None,
                  progress: Callable[[str], None] | None = None, force: bool = False) -> dict:
    """Put one backup (folder, ``.zip``, ``.tgz``/``.tar``) into the library. Returns a summary."""
    say = progress or (lambda msg: None)
    if mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}")
    library = ensure_library(library or default_library_dir())
    if not os.path.exists(source):
        raise FileNotFoundError(source)
    if os.path.isdir(source):
        root = find_server_root(source)
        if os.path.normcase(os.path.abspath(root)).startswith(os.path.normcase(library) + os.sep):
            raise ValueError(f"{source} is already inside the library")
        say(f"Importing {source}")
        return _import_root(root, library, mode, world, source, say, force)
    if not (zipfile.is_zipfile(source) or _is_tar(source)):
        raise ValueError(f"{source} is not a folder, .zip or .tgz/.tar backup")
    # Unpack next to the library (same drive), then move the files into place.
    staging_parent = os.path.join(library, ".incoming")
    os.makedirs(staging_parent, exist_ok=True)
    staging = tempfile.mkdtemp(prefix="import-", dir=staging_parent)
    try:
        say(f"Unpacking {source} (big backups take a few minutes)")
        (_extract_zip if zipfile.is_zipfile(source) else _extract_tar)(source, staging, say)
        root = find_server_root(staging)
        say("Sorting it into the library")
        return _import_root(root, library, "move", world, source, say, force)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        try:
            os.rmdir(staging_parent)
        except OSError:
            pass


def add_recordings(paths: list[str], world_dir: str, mode: str = "auto",
                   progress: Callable[[str], None] | None = None) -> dict:
    """Add loose recordings (``.zip``/``.mcpr`` files or folders of them) to a library world.

    A folder keeps its layout under ``recordings/imported/<folder name>/`` (so ServerReplay's
    ``players/<uuid>/`` folders still say whose recording it is). A recording already anywhere in
    the world (same file name, identical content) is not stored again.
    """
    say = progress or (lambda msg: None)
    if not is_world_dir(world_dir):
        raise ValueError(f"{world_dir} is not a library world folder")
    dest_root = os.path.join(world_dir, "recordings", "imported")
    index = _recording_index(os.path.join(world_dir, "recordings"))
    stats = {"linked": 0, "copied": 0, "moved": 0, "bytes_written": 0, "added": 0, "known": 0,
             "deduplicated": 0}
    for src in paths:
        src = os.path.abspath(src)
        if os.path.isdir(src) and not (_is_flashback_dir(src) or _is_replaymod_dir(src)):
            found = [p for _k, p in find_replay_sources(src)]
            base_dir = os.path.join(dest_root, _safe(os.path.basename(src.rstrip("/\\"))))
            if not found:
                say(f"  no recordings found in {src}")
        else:
            found, base_dir, src = [src], dest_root, os.path.dirname(src)
        for p in found:
            target = os.path.join(base_dir, os.path.relpath(p, src))
            m = mode if mode != "auto" else ("link" if _same_drive(p, world_dir) else "copy")
            files = list(_walk_files(p)) if os.path.isdir(p) else [p]
            if not os.path.isdir(p):
                if _find_twin(index, p, os.path.getsize(p)):
                    stats["known"] += 1
                    continue
                if os.path.exists(target):
                    stem, ext = os.path.splitext(target)
                    k = 2
                    while os.path.exists(f"{stem} ({k}){ext}"):
                        k += 1
                    target = f"{stem} ({k}){ext}"
            elif os.path.exists(target):
                stats["known"] += 1
                continue
            for f in files:
                t = target if f == p else os.path.join(target, os.path.relpath(f, p))
                size = os.path.getsize(f)
                twin = _find_twin(index, f, size) if f != p else None
                if twin and _link(twin, t):
                    stats["deduplicated"] += 1
                else:
                    _place(f, t, m, stats)
                index.setdefault((os.path.basename(t).lower(), size), []).append(t)
            stats["added"] += 1
            if stats["added"] % 50 == 0:
                say(f"  {stats['added']} added, {stats['known']} already there")
    return stats


def human_bytes(n: int | float | None) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
