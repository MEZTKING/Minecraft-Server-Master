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
        if not name.startswith(".") and os.path.isfile(os.path.join(p, BACKUP_MARKER)):
            out.append({**_read_json(os.path.join(p, BACKUP_MARKER)), "name": name, "path": p})
    out.sort(key=lambda b: (b.get("snapshot_ms") or 0, b["name"]))
    return out


def world_recordings(world_dir: str) -> list[str]:
    rec = os.path.join(world_dir, "recordings")
    return [p for _k, p in find_replay_sources(rec)] if os.path.isdir(rec) else []


def list_worlds(library: str, count_recordings: bool = True) -> list[dict]:
    out = []
    for name in sorted(os.listdir(library)) if os.path.isdir(library) else []:
        p = os.path.join(library, name)
        if is_world_dir(p):
            out.append({**_read_json(os.path.join(p, WORLD_MARKER)), "name": name, "path": p,
                        "backups": world_backups(p),
                        "recordings": len(world_recordings(p)) if count_recordings else None})
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
#
# Safety rules (see docs/LIBRARY.md):
# * In copy and link mode the source is only read. Nothing is ever written through a library
#   path that could be a hard link: every write creates a new file (``*.part`` + rename).
# * A backup is built in ``backups/.partial-*`` and renamed into place only when complete, so
#   a failed or interrupted import never leaves a half backup that looks finished.
# * Directory walks raise on errors instead of silently leaving files out.

_LONG = "\\\\?\\"


def _lp(p: str) -> str:
    """A path Windows can open even when it is longer than 260 characters."""
    if os.name != "nt" or p.startswith(_LONG):
        return p
    p = os.path.abspath(p)
    if len(p) < 240:
        return p
    return _LONG + "UNC\\" + p[2:] if p.startswith("\\\\") else _LONG + p


def _unlp(p: str) -> str:
    if p.startswith(_LONG + "UNC\\"):
        return "\\\\" + p[len(_LONG) + 4:]
    return p[len(_LONG):] if p.startswith(_LONG) else p


def _raise(err: OSError):
    raise err


def _walk_files(top: str):
    """Every file below *top*, sorted. Raises if a folder can't be read (nothing is skipped)."""
    root = top
    if os.name == "nt" and not top.startswith(_LONG):
        root = _LONG + ("UNC\\" + os.path.abspath(top)[2:] if os.path.abspath(top).startswith("\\\\")
                        else os.path.abspath(top))
    for cur, dirs, files in os.walk(root, onerror=_raise):
        dirs.sort()
        for f in sorted(files):
            yield _unlp(os.path.join(cur, f)) if root != top else os.path.join(cur, f)


def _size(p: str) -> int:
    return os.path.getsize(_lp(p))


def _has_server_files(d: str) -> bool:
    return any(os.path.exists(os.path.join(d, m)) for m in ("server.properties", "logs", "usercache.json"))


def _is_server_dir(d: str) -> bool:
    """A server root: ``server.properties``, or ``logs/`` with ``eula.txt`` / ``usercache.json``.
    A game launcher folder (``logs/`` + ``saves/`` / ``options.txt``) is not a server."""
    if os.path.isfile(os.path.join(d, "server.properties")):
        return True
    if not os.path.isdir(os.path.join(d, "logs")):
        return False
    if os.path.isdir(os.path.join(d, "saves")) or os.path.isfile(os.path.join(d, "options.txt")):
        return False
    return os.path.isfile(os.path.join(d, "eula.txt")) or os.path.isfile(os.path.join(d, "usercache.json"))


def _is_world_folder(d: str) -> bool:
    return os.path.isfile(os.path.join(d, "level.dat")) and (
        os.path.isdir(os.path.join(d, "region")) or os.path.isdir(os.path.join(d, "dimensions"))
        or os.path.isdir(os.path.join(d, "playerdata")) or os.path.isdir(os.path.join(d, "players")))


def locate(path: str) -> tuple[str, bool]:
    """``(root, world_only)`` for an unpacked backup: the server root, or -- for a backup that
    holds only a world (Aternos/Realms downloads, singleplayer saves) -- the world folder."""
    root = find_server_root(path)
    if _is_server_dir(root):
        return root, False
    for cur, dirs, _files in os.walk(path):
        if _is_server_dir(cur):
            return cur, False
        if _is_world_folder(cur):
            return cur, True
        depth = os.path.relpath(cur, path).count(os.sep) + (cur != path)
        if depth >= 3:
            dirs[:] = []
        dirs.sort()
    raise ValueError(f"no Minecraft server or world found in {path}")


def _probe(root: str, world_only: bool = False) -> dict:
    """Level name, seed, version and snapshot time of an (extracted) server, cheaply."""
    props = {} if world_only else world_mod.read_properties(os.path.join(root, "server.properties"))
    wd = root if world_only else world_mod.world_dir(root, props)
    lvl = world_mod.read_level_dat(wd) if os.path.isdir(wd) else {}
    now_ms = time.time() * 1000 + 86400_000
    times = []
    lp = lvl.get("last_played_ms")
    if isinstance(lp, (int, float)) and 0 < lp < now_ms:         # garbage values are ignored
        times.append(int(lp))
    latest = os.path.join(root, "logs", "latest.log")
    if os.path.isfile(latest):
        times.append(int(os.path.getmtime(latest) * 1000))
    if not times and os.path.isfile(os.path.join(wd, "level.dat")):   # no LastPlayed, no logs
        times.append(int(os.path.getmtime(os.path.join(wd, "level.dat")) * 1000))
    return {"level_name": lvl.get("level_name") or props.get("level-name") or
                          (os.path.basename(root) if world_only else "world"),
            "seed": lvl.get("seed"), "version": lvl.get("version_name"),
            "snapshot_ms": max(times) if times else None}


def _find_or_make_world(library: str, info: dict, name: str | None) -> str:
    level, seed = info["level_name"], info["seed"]
    if name:
        w = os.path.join(library, _safe(name))
    else:
        w = None
        for x in list_worlds(library, count_recordings=False):
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
        return os.stat(_lp(a)).st_dev == os.stat(_lp(b)).st_dev
    except OSError:
        return False


def _new_file(dst: str, write: Callable[[str], None]) -> None:
    """Create *dst* via ``dst.part`` + rename, so a half-written file never has the real name."""
    part = dst + ".part"
    try:
        write(_lp(part))
        os.replace(_lp(part), _lp(dst))
    except BaseException:
        try:
            os.remove(_lp(part))
        except OSError:
            pass
        raise


def _place(src: str, dst: str, mode: str, stats: dict) -> None:
    os.makedirs(_lp(os.path.dirname(dst)), exist_ok=True)
    size = _size(src)
    if mode == "link":
        try:
            os.link(_lp(src), _lp(dst))
            stats["linked"] += 1
            return
        except OSError:
            pass                     # other drive, FAT32, link limit: copy instead
    if mode == "move":
        try:
            os.replace(_lp(src), _lp(dst))             # same drive: instant
        except OSError:
            _new_file(dst, lambda part: shutil.copy2(_lp(src), part))   # other drive: copy, then delete
            os.remove(_lp(src))
            stats["bytes_written"] += size
        stats["moved"] += 1
        return
    _new_file(dst, lambda part: shutil.copy2(_lp(src), part))
    stats["copied"] += 1
    stats["bytes_written"] += size


def _identical(a: str, b: str) -> bool:
    try:
        sa, sb = os.stat(_lp(a)), os.stat(_lp(b))
    except OSError:
        return False
    if sa.st_size != sb.st_size:
        return False
    if sa.st_ino and sa.st_ino == sb.st_ino and sa.st_dev == sb.st_dev:
        return True                                    # the same file (hard link)
    return filecmp.cmp(_lp(a), _lp(b), shallow=False)


def _recording_index(rec_root: str) -> dict[tuple[str, int], list[str]]:
    """(file name, size) -> paths of the recording files already in a world."""
    index: dict[tuple[str, int], list[str]] = {}
    if os.path.isdir(rec_root):
        for p in _walk_files(rec_root):
            index.setdefault((os.path.basename(p).lower(), _size(p)), []).append(p)
    return index


def _folder_index(rec_root: str) -> dict[str, list[str]]:
    """Folder name -> recording folders already in a world (for unfinished recordings)."""
    index: dict[str, list[str]] = {}
    if os.path.isdir(rec_root):
        for cur, dirs, _files in os.walk(rec_root):
            for d in dirs:
                index.setdefault(d.lower(), []).append(os.path.join(cur, d))
    return index


def _same_folder(a: str, b: str) -> bool:
    """Two recording folders with the same files and identical contents."""
    try:
        fa = sorted(os.path.relpath(f, a) for f in _walk_files(a))
        fb = sorted(os.path.relpath(f, b) for f in _walk_files(b))
    except OSError:
        return False
    return fa == fb and all(_identical(os.path.join(a, f), os.path.join(b, f)) for f in fa)


def _find_twin(index: dict, path: str, size: int) -> str | None:
    return next((c for c in index.get((os.path.basename(path).lower(), size), []) if _identical(path, c)), None)


def _variant(target: str, is_same: Callable[[str], bool], folder: bool = False) -> tuple[str, bool]:
    """Where a recording goes: ``(path, already_there)``. Checks ``name``, ``name (2)``, …
    so a recording that differs from the stored one is kept once, not once per backup."""
    stem, ext = (target, "") if folder else os.path.splitext(target)
    k = 1
    while True:
        cand = target if k == 1 else f"{stem} ({k}){ext}"
        if not os.path.exists(_lp(cand)):
            return cand, False
        if is_same(cand):
            return cand, True
        k += 1


def _link(existing: str, target: str) -> bool:
    try:
        os.makedirs(_lp(os.path.dirname(target)), exist_ok=True)
        os.link(_lp(existing), _lp(target))
        return True
    except OSError:
        return False


def _replay_paths(root: str) -> list[str]:
    """Replay files/folders in a server, relative to it ('/'-separated)."""
    return [os.path.relpath(p, root).replace(os.sep, "/") for _k, p in find_replay_sources(root)]


def _clean_stale(library: str, say: Callable[[str], None]) -> None:
    """Remove leftovers of imports that were interrupted more than a day ago."""
    cutoff = time.time() - 86400
    spots = [os.path.join(library, ".incoming")]
    for w in list_worlds(library, count_recordings=False):
        spots.append(os.path.join(w["path"], "backups"))
    for spot in spots:
        for name in os.listdir(spot) if os.path.isdir(spot) else []:
            p = os.path.join(spot, name)
            if (name.startswith("import-") or name.startswith(".partial-")) and os.path.getmtime(p) < cutoff:
                say(f"Removing the leftovers of an interrupted import: {p}")
                shutil.rmtree(_lp(p), ignore_errors=True)


def _import_root(root: str, library: str, mode: str, world_name: str | None, source: str,
                 say: Callable[[str], None], force: bool = False, world_only: bool = False,
                 world_folder: str | None = None) -> dict:
    info = _probe(root, world_only)
    # A world-only backup becomes <backup>/<world folder>/ plus a generated server.properties.
    prefix = _safe(world_folder or os.path.basename(os.path.normpath(root))) + "/" if world_only else ""
    if world_only and world_folder:
        info["level_name"] = info["level_name"] if info["level_name"] != os.path.basename(root) else world_folder
    w = _find_or_make_world(library, info, world_name)
    when = info["snapshot_ms"]
    try:
        stamp = datetime.fromtimestamp(when / 1000).strftime("%Y-%m-%d %H-%M") if when else "undated"
    except (OverflowError, OSError, ValueError):
        stamp, when = "undated", None
    if mode == "auto":
        mode = "link" if _same_drive(root, w) else "copy"

    files = []
    for p in _walk_files(root):
        rel = os.path.relpath(p, root).replace(os.sep, "/")
        if rel not in (BACKUP_MARKER, "mcsm_stub.json"):
            files.append((p, rel, _size(p)))
    src_files, src_bytes = len(files), sum(s for _p, _r, s in files)

    # Same moment = same backup (2 s tolerance: .zip stores times to 2 seconds). If the copy in
    # the library is at least as complete, only its recordings are merged; a more complete copy
    # (e.g. the earlier one was unpacked only partly) is imported as well.
    recordings_only, supersedes = False, None
    same_time = [b for b in world_backups(w) if when and b.get("snapshot_ms") and not force
                 and abs(b["snapshot_ms"] - when) <= 2000]
    for b in same_time:
        if (b.get("files") or 0) >= src_files and (b.get("bytes") or 0) >= src_bytes * 0.999:
            recordings_only, existing = True, b
            say(f"Already in the library: {b['path']} (only new recordings are added)")
            break
    if same_time and not recordings_only:
        supersedes = same_time[-1]["path"]
        say(f"A less complete copy of this backup is in the library ({supersedes}); importing this one too")

    backups_dir = os.path.join(w, "backups")
    partial = tempfile.mkdtemp(prefix=".partial-", dir=backups_dir) if not recordings_only else None

    # Index of the world's existing files for de-duplication: (rel, size) -> paths (one per file).
    have: dict[tuple[str, int], list[str]] = {}
    if not recordings_only:
        seen_inodes = set()
        for b in world_backups(w):
            for p in _walk_files(b["path"]):
                st = os.stat(_lp(p))
                if st.st_ino and (st.st_dev, st.st_ino) in seen_inodes:
                    continue
                seen_inodes.add((st.st_dev, st.st_ino))
                rel = os.path.relpath(p, b["path"]).replace(os.sep, "/")
                if rel != BACKUP_MARKER:
                    have.setdefault((rel, st.st_size), []).append(p)

    stats = {"files": 0, "linked": 0, "copied": 0, "moved": 0, "deduplicated": 0, "bytes_written": 0,
             "recordings_new": 0, "recordings_known": 0, "bytes": 0}
    replays = _replay_paths(root)
    rec_root = os.path.join(w, "recordings")
    replay_set = set(replays)
    rec_index = _recording_index(rec_root)
    folder_index = _folder_index(rec_root)

    def in_replay(rel: str) -> str | None:
        parts = rel.split("/")
        for i in range(1, len(parts) + 1):
            head = "/".join(parts[:i])
            if head in replay_set:
                return head
        return None

    # Decide once per recording where it goes (or that it's already there).
    plan: dict[str, str | None] = {}
    for r in replays:
        src = os.path.join(root, *r.split("/"))
        target = os.path.join(rec_root, *r.split("/"))
        if os.path.isdir(src):
            dest_dir, known = _variant(target, lambda c: _same_folder(src, c), folder=True)
            if not known:   # the same unfinished recording under another player-folder name?
                known = any(_same_folder(src, c) for c in folder_index.get(os.path.basename(src).lower(), []))
            plan[r] = None if known else dest_dir
        else:
            size = _size(src)
            dest_file, known = _variant(target, lambda c: _identical(src, c))
            if not known and _find_twin(rec_index, src, size):
                known = True                        # same recording stored under another folder name
            plan[r] = None if known else dest_file
        if plan[r] is None:
            stats["recordings_known"] += 1
        else:
            stats["recordings_new"] += 1

    try:
        t0, last = time.monotonic(), 0.0
        for p, rel, size in files:
            r = in_replay(rel)
            stats["files"] += 1
            stats["bytes"] += size
            if r is not None:                               # a recording: stored once per world
                dest_r = plan[r]
                if dest_r is None:
                    if mode == "move":
                        os.remove(_lp(p))
                    continue
                target = dest_r if rel == r else os.path.join(dest_r, *rel[len(r) + 1:].split("/"))
                twin = _find_twin(rec_index, p, size) if rel != r else None
                if twin and _link(twin, target):            # a chunk shared with another copy
                    stats["deduplicated"] += 1
                    if mode == "move":
                        os.remove(_lp(p))
                else:
                    _place(p, target, mode, stats)
                rec_index.setdefault((os.path.basename(target).lower(), size), []).append(target)
            elif not recordings_only:
                rel = prefix + rel
                target = os.path.join(partial, *rel.split("/"))
                twin = next((c for c in have.get((rel, size), []) if _identical(p, c)), None)
                if twin and _link(twin, target):
                    stats["deduplicated"] += 1
                    if mode == "move":
                        os.remove(_lp(p))
                else:
                    _place(p, target, mode, stats)
            now = time.monotonic()
            if now - last > 2:
                last = now
                say(f"  {stats['files']:,} files, {stats['bytes'] / 1e9:.1f} GB ({now - t0:.0f} s)")
        if recordings_only:
            return {"world": w, "backup": existing["path"], "skipped": True, **info, **stats}
        if world_only:
            with open(os.path.join(partial, "server.properties"), "w", encoding="utf-8") as fh:
                fh.write("# written by mcsm: this backup contained only the world folder\n"
                         f"level-name={prefix.rstrip('/')}\n")
        meta = {"world_only": world_only, "source": os.path.abspath(source), **_source_sig(source),
                "source_root": os.path.abspath(root) if os.path.isdir(source) else None,
                "imported": datetime.now(timezone.utc).isoformat(), "mode": mode, "snapshot_ms": when,
                "level_name": info["level_name"], "seed": info["seed"], "version": info["version"], **stats}
        if supersedes:
            meta["more_complete_than"] = supersedes
        _write_json(os.path.join(partial, BACKUP_MARKER), meta)
        dest, n = os.path.join(backups_dir, stamp), 2
        while os.path.exists(dest):
            dest, n = os.path.join(backups_dir, f"{stamp} ({n})"), n + 1
        os.rename(partial, dest)                            # only now does it count as a backup
        partial = None
        return {"world": w, "backup": dest, "skipped": False, **meta}
    finally:
        if partial:
            shutil.rmtree(_lp(partial), ignore_errors=True)


def _source_sig(source: str) -> dict:
    """Size and time of an imported archive, so a later scan knows it without unpacking it."""
    try:
        if os.path.isfile(source):
            return {"source_size": os.path.getsize(source), "source_mtime": int(os.path.getmtime(source))}
    except OSError:
        pass
    return {}


def imported_sources(library: str) -> list[dict]:
    """Every backup in the library with where it was imported from."""
    out = []
    for w in list_worlds(library, count_recordings=False) if os.path.isdir(library) else []:
        for b in w["backups"]:
            if b.get("source"):
                out.append(b)
    return out


def _already_imported(c: dict, imported: list[dict]) -> str | None:
    """The library backup a scanned backup was imported as (same file, unchanged), if any."""
    norm = lambda p: os.path.normcase(os.path.abspath(p)) if p else ""  # noqa: E731
    path = norm(c["path"])
    for b in imported:
        src = norm(b.get("source"))
        if c["kind"] == "folder":
            roots = {norm(b.get("source_root")), src}
            if path in roots or (src and path.startswith(src.rstrip(os.sep) + os.sep)):
                return b["path"]
        elif path == src:
            same_size = b.get("source_size") in (None, c.get("size"))
            same_time = b.get("source_mtime") is None or abs(b["source_mtime"] - os.path.getmtime(c["path"])) <= 2
            if same_size and same_time:
                return b["path"]
    return None


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


_RESERVED = {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}


def _local_name(name: str) -> str:
    """An archive member name that Windows can create: ``Recordings for: X`` -> ``Recordings for_ X``
    (what Windows unpacking tools do too, and what the replay reader expects)."""
    if os.name != "nt":
        return name
    bad = '[<>:"|?*' + "".join(chr(c) for c in range(32)) + "]"
    parts = []
    for p in name.replace(chr(92), "/").split("/"):
        p = re.sub(bad, "_", p).rstrip(" .") or "_"
        if p.split(".")[0].lower() in _RESERVED:
            p = "_" + p
        parts.append(p)
    return "/".join(parts)


def _free_target(target: str, warn: dict) -> str:
    """Two archive members that land on the same file (``Foo``/``foo``, ``a:b``/``a_b``): keep both."""
    if not os.path.exists(_lp(target)):
        return target
    warn["renamed"] = warn.get("renamed", 0) + 1
    stem, ext = os.path.splitext(target)
    k = 2
    while os.path.exists(_lp(f"{stem} ({k}){ext}")):
        k += 1
    return f"{stem} ({k}){ext}"


def _extract_tar(path: str, dest: str, say: Callable[[str], None]) -> None:
    dest_real = os.path.realpath(dest)
    warn: dict[str, int] = {}
    done: dict[str, str] = {}                       # member name -> extracted file (for hard links)
    with open(path, "rb") as raw:
        wrapped = _Progress(raw, os.path.getsize(path), say)
        with tarfile.open(fileobj=wrapped, mode="r|*") as tf:
            for member in tf:
                name = _local_name(member.name)
                if not _is_safe(dest_real, name):
                    warn["unsafe"] = warn.get("unsafe", 0) + 1      # would land outside: never extracted
                    continue
                target = os.path.join(dest, name)
                if member.isdir():
                    os.makedirs(_lp(target), exist_ok=True)
                    continue
                if member.islnk():                  # a hard link to a file earlier in the archive
                    src = done.get(member.linkname)
                    if src and os.path.isfile(_lp(src)):
                        os.makedirs(_lp(os.path.dirname(target)), exist_ok=True)
                        target = _free_target(target, warn)
                        shutil.copy2(_lp(src), _lp(target))
                        done[member.name] = target
                    else:
                        warn["links"] = warn.get("links", 0) + 1
                    continue
                if not member.isfile():
                    warn["special"] = warn.get("special", 0) + 1    # symlinks, devices: skipped
                    continue
                os.makedirs(_lp(os.path.dirname(target)), exist_ok=True)
                target = _free_target(target, warn)
                src = tf.extractfile(member)
                if src is None:
                    continue
                with src, open(_lp(target), "wb") as out:
                    shutil.copyfileobj(src, out, 1 << 20)
                done[member.name] = target
                try:
                    os.utime(_lp(target), (member.mtime, member.mtime))
                except (OSError, OverflowError, ValueError):
                    pass
    _say_warnings(warn, say)


def _say_warnings(warn: dict, say: Callable[[str], None]) -> None:
    if warn.get("unsafe"):
        say(f"  ! skipped {warn['unsafe']} entries that would land outside the folder")
    if warn.get("links"):
        say(f"  ! skipped {warn['links']} hard links whose target wasn't in the archive")
    if warn.get("special"):
        say(f"  ! skipped {warn['special']} symbolic links / special files")
    if warn.get("renamed"):
        say(f"  ! {warn['renamed']} files had names Windows treats as the same; kept as 'name (2)'")


def _extract_zip(path: str, dest: str, say: Callable[[str], None]) -> None:
    dest_real = os.path.realpath(dest)
    warn: dict[str, int] = {}
    with zipfile.ZipFile(path) as zf:
        infos = zf.infolist()
        total = sum(i.file_size for i in infos) or 1
        done, last = 0, 0.0
        for info in infos:
            name = _local_name(info.filename)
            if info.is_dir():
                continue
            if not _is_safe(dest_real, name):
                warn["unsafe"] = warn.get("unsafe", 0) + 1
                continue
            target = os.path.join(dest, name)
            os.makedirs(_lp(os.path.dirname(target)), exist_ok=True)
            target = _free_target(target, warn)
            with zf.open(info) as src, open(_lp(target), "wb") as out:
                shutil.copyfileobj(src, out, 1 << 20)
            _restore_mtime(_lp(target), info)
            done += info.file_size
            if time.monotonic() - last > 3:
                last = time.monotonic()
                say(f"  unpacking {100 * done / total:.0f}%")
    _say_warnings(warn, say)


def _inside(a: str, b: str) -> bool:
    """Is *a* the same as or inside *b* (after resolving links, junctions and subst drives)?"""
    a, b = os.path.normcase(os.path.realpath(a)), os.path.normcase(os.path.realpath(b))
    return a == b or a.startswith(b.rstrip(os.sep) + os.sep)


def import_backup(source: str, library: str | None = None, mode: str = "auto", world: str | None = None,
                  progress: Callable[[str], None] | None = None, force: bool = False) -> dict:
    """Put one backup (folder, ``.zip``, ``.tgz``/``.tar``) into the library. Returns a summary."""
    say = progress or (lambda msg: None)
    if mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}")
    library = ensure_library(library or default_library_dir())
    if not os.path.exists(source):
        raise FileNotFoundError(source)
    if _inside(source, library) or _inside(library, source):
        raise ValueError(f"{source} and the library {library} overlap: keep the library in its own folder")
    _clean_stale(library, say)
    if os.path.isdir(source):
        root, world_only = locate(source)
        say(f"Importing {source}")
        return _import_root(root, library, mode, world, source, say, force, world_only)
    # By name first: a .tar whose last member is a recording (a zip) also passes is_zipfile().
    kind = _archive_kind(os.path.basename(source))
    if kind is None:
        kind = "tar" if _is_tar(source) else "zip" if zipfile.is_zipfile(source) else None
    if kind is None or (kind == "zip" and not zipfile.is_zipfile(source)):
        raise ValueError(f"{source} is not a folder, .zip or .tgz/.tar backup")
    # Unpack next to the library (same drive), then move the files into place.
    staging_parent = os.path.join(library, ".incoming")
    os.makedirs(staging_parent, exist_ok=True)
    staging = tempfile.mkdtemp(prefix="import-", dir=staging_parent)
    try:
        say(f"Unpacking {source} (big backups take a few minutes)")
        (_extract_zip if kind == "zip" else _extract_tar)(source, staging, say)
        root, world_only = locate(staging)
        say("Sorting it into the library")
        name = _stem(os.path.basename(source)) if world_only and os.path.samefile(root, staging) else None
        return _import_root(root, library, "move", world, source, say, force, world_only, name)
    finally:
        shutil.rmtree(_lp(staging), ignore_errors=True)
        try:
            os.rmdir(staging_parent)
        except OSError:
            pass


def add_recordings(paths: list[str], world_dir: str, mode: str = "copy",
                   progress: Callable[[str], None] | None = None, base: str | None = None) -> dict:
    """Add loose recordings (``.zip``/``.mcpr`` files or folders of them) to a library world.

    Copies by default (``auto`` means copy here too): recordings often sit in a live game
    folder, which shouldn't share data with the library. A folder keeps its layout under
    ``recordings/imported/<folder name>/`` (so ServerReplay's ``players/<uuid>/`` folders still
    say whose recording it is). A recording already anywhere in the world (same file name,
    identical content) is not stored again.
    """
    say = progress or (lambda msg: None)
    if not is_world_dir(world_dir):
        raise ValueError(f"{world_dir} is not a library world folder")
    mode = "copy" if mode == "auto" else mode
    rec_root = os.path.join(world_dir, "recordings")
    dest_root = os.path.join(rec_root, "imported")
    index = _recording_index(rec_root)
    folders = _folder_index(rec_root)
    stats = {"linked": 0, "copied": 0, "moved": 0, "bytes_written": 0, "added": 0, "known": 0,
             "deduplicated": 0}
    for src in paths:
        src = os.path.abspath(src)
        if _inside(src, world_dir):
            raise ValueError(f"{src} is inside the library world already")
        if os.path.isdir(src) and not (_is_flashback_dir(src) or _is_replaymod_dir(src)):
            found = [p for _k, p in find_replay_sources(src)]
            base_dir = os.path.join(dest_root, _safe(os.path.basename(src.rstrip("/" + chr(92)))))
            if not found:
                say(f"  no recordings found in {src}")
        elif base:                                  # keep the layout below *base*
            found, src = [src], os.path.abspath(base)
            base_dir = os.path.join(dest_root, _safe(os.path.basename(src.rstrip("/" + chr(92)))))
        else:
            found, base_dir, src = [src], dest_root, os.path.dirname(src)
        for p in found:
            target = os.path.join(base_dir, os.path.relpath(p, src))
            if os.path.isdir(p):
                if any(_same_folder(p, c) for c in folders.get(os.path.basename(p).lower(), [])):
                    stats["known"] += 1
                    continue
                target, known = _variant(target, lambda c: _same_folder(p, c), folder=True)
                if known:
                    stats["known"] += 1
                    continue
                for f in _walk_files(p):
                    t = os.path.join(target, os.path.relpath(f, p))
                    size = _size(f)
                    twin = _find_twin(index, f, size)
                    if twin and _link(twin, t):
                        stats["deduplicated"] += 1
                    else:
                        _place(f, t, mode, stats)
                    index.setdefault((os.path.basename(t).lower(), size), []).append(t)
                folders.setdefault(os.path.basename(target).lower(), []).append(target)
            else:
                size = _size(p)
                if _find_twin(index, p, size):
                    stats["known"] += 1
                    continue
                target, known = _variant(target, lambda c: _identical(p, c))
                if known:
                    stats["known"] += 1
                    continue
                _place(p, target, mode, stats)
                index.setdefault((os.path.basename(target).lower(), size), []).append(target)
            stats["added"] += 1
            if stats["added"] % 50 == 0:
                say(f"  {stats['added']} added, {stats['known']} already there")
    return stats


# --- importing a whole folder ---------------------------------------------------------

_UNSUPPORTED = (".7z", ".rar")
_PREFER = {"folder": 0, "tar": 1, "zip": 2, "tgz": 3}


def _archive_kind(name: str) -> str | None:
    low = name.lower()
    if low.endswith((".tgz", ".tar.gz")):
        return "tgz"
    if low.endswith(".tar"):
        return "tar"
    if low.endswith(".zip"):
        return "zip"
    return None


def _stem(name: str) -> str:
    low = name.lower()
    for ext in (".tar.gz", ".tgz", ".tar", ".zip"):
        if low.endswith(ext):
            return name[: -len(ext)]
    return name


def _zip_role(path: str) -> str:
    """'backup', 'recording' or 'other' for a .zip, from its file list (cheap)."""
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
    except (zipfile.BadZipFile, OSError):
        return "other"
    base = {n.rstrip("/").rsplit("/", 1)[-1] for n in names}
    if "metaData.json" in base or ("metadata.json" in base and any(n.endswith(".flashback") for n in names)):
        return "recording"
    if "server.properties" in base or "level.dat" in base:
        return "backup"
    return "other"


def _fingerprint(path: str) -> tuple:
    """Size + hash of the first and last MiB: identical archives match, different ones don't."""
    import hashlib
    size = os.path.getsize(path)
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        h.update(fh.read(1 << 20))
        for k in range(1, 17):                  # 16 more samples spread over the whole file
            fh.seek(max(0, size * k // 17 - (1 << 15)))
            h.update(fh.read(1 << 16))
        if size > 2 << 20:
            fh.seek(-(1 << 20), os.SEEK_END)
            h.update(fh.read(1 << 20))
    return size, h.hexdigest()


def _gzip_isize(path: str) -> int | None:
    """Uncompressed size (mod 4 GiB) stored in a .gz file's last 4 bytes."""
    try:
        with open(path, "rb") as fh:
            if fh.read(2) != b"\x1f\x8b":
                return None
            fh.seek(-4, os.SEEK_END)
            return int.from_bytes(fh.read(4), "little")
    except OSError:
        return None


def _folder_bytes(path: str) -> int:
    total = 0
    for f in _walk_files(path):
        total += _size(f)
    return total


def _archive_bytes(a: dict) -> int | None:
    """Uncompressed content size of an archive when it is cheap to know (zip listing, plain tar)."""
    if a["kind"] == "zip":
        try:
            with zipfile.ZipFile(a["path"]) as zf:
                return sum(i.file_size for i in zf.infolist())
        except (zipfile.BadZipFile, OSError):
            return None
    if a["kind"] == "tar":
        return a["size"]
    return None


def _when(path: str, kind: str) -> float:
    if kind == "folder":
        for rel in ("logs/latest.log", "level.dat"):
            p = os.path.join(path, *rel.split("/"))
            if os.path.isfile(p):
                return os.path.getmtime(p)
    return os.path.getmtime(path)


def scan(folder: str, library: str | None = None) -> dict:
    """Find everything importable under *folder* and plan the import. Changes nothing.

    Found by content, not by hosting company: server folders, world-only folders (Aternos /
    Realms downloads, singleplayer saves), archives (.zip checked by its file list; .tgz/.tar.gz/.tar
    checked when unpacked) and recordings. Returns ``{"backups", "same", "recordings",
    "unsupported", "ignored", "ignored_bytes"}``; ``same`` lists copies of a backup that are skipped.
    """
    from .replays import _zip_kind
    folder = os.path.abspath(folder)
    lib = os.path.normcase(os.path.abspath(library)) if library else None
    cands: list[dict] = []
    recs: dict[str, list[str]] = {}
    unsupported: list[str] = []
    ignored: dict[str, int] = {}
    ignored_bytes = 0

    def group_of(p: str) -> str:
        rel = os.path.relpath(p, folder).split(os.sep)
        return folder if len(rel) == 1 else os.path.join(folder, rel[0])

    if _is_server_dir(folder) or _is_world_folder(folder):
        cands.append({"path": folder, "kind": "folder", "size": None, "world_only": not _is_server_dir(folder)})
    walk = [] if cands else os.walk(folder)
    for cur, dirs, files in walk:
        if lib and (os.path.normcase(cur) == lib or os.path.normcase(cur).startswith(lib + os.sep)):
            dirs[:] = []
            continue
        if cur != folder and _is_server_dir(cur):
            cands.append({"path": cur, "kind": "folder", "size": None, "world_only": False})
            dirs[:] = []                       # a server: never look inside it for more backups
            continue
        if cur != folder and _is_world_folder(cur):
            cands.append({"path": cur, "kind": "folder", "size": None, "world_only": True})
            dirs[:] = []
            continue
        keep = []
        for d in sorted(dirs):
            full = os.path.join(cur, d)
            if _is_flashback_dir(full) or _is_replaymod_dir(full):
                recs.setdefault(group_of(full), []).append(full)
            elif not d.startswith((".", "$")):
                keep.append(d)
        dirs[:] = keep
        in_replay_area = re.search(r"record|replay|flashback", cur, re.IGNORECASE)
        for f in sorted(files):
            full = os.path.join(cur, f)
            low = f.lower()
            kind = _archive_kind(f)
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            if low.endswith(".mcpr"):
                recs.setdefault(group_of(full), []).append(full)
            elif kind == "zip":
                role = _zip_role(full)
                if role == "recording" and (in_replay_area or _zip_kind(full)):
                    recs.setdefault(group_of(full), []).append(full)
                elif role == "backup":
                    cands.append({"path": full, "kind": "zip", "size": size, "world_only": None})
                else:
                    ignored[".zip"] = ignored.get(".zip", 0) + 1
                    ignored_bytes += size
            elif kind in ("tar", "tgz"):
                cands.append({"path": full, "kind": kind, "size": size, "world_only": None})
            elif low.endswith(_UNSUPPORTED):
                unsupported.append(full)
            else:
                ext = os.path.splitext(low)[1] or "(none)"
                ignored[ext] = ignored.get(ext, 0) + 1
                ignored_bytes += size

    # One copy per backup: folder > .tar > .zip > .tgz.
    folders = [c for c in cands if c["kind"] == "folder"]
    archives = [c for c in cands if c["kind"] != "folder"]
    for a in archives:                          # unpacked copy: a folder named like the archive, next to it
        adir, stem = os.path.dirname(a["path"]), _stem(os.path.basename(a["path"]))
        for f in folders:
            d = f["path"]
            while len(d) > len(adir):
                if os.path.basename(d) == stem and os.path.dirname(d) == adir:
                    # Complete? Compare with the archive: content size for zip/tar; for .tgz the
                    # compressed size (Minecraft data is mostly compressed already, so a complete
                    # folder is never much smaller than its .tgz).
                    have_bytes = _folder_bytes(d)
                    want = _archive_bytes(a)
                    ok = have_bytes >= 0.97 * want if want else have_bytes >= 0.9 * a["size"]
                    if ok:
                        a["same_as"], a["why"] = f["path"], "unpacked copy next to it"
                    else:
                        f["same_as"], f["why"] = a["path"], "incomplete unpacked copy: the archive is used"
                    break
                d = os.path.dirname(d)
            if a.get("same_as") or f.get("same_as") == a["path"]:
                break
    loose = [a for a in archives if not a.get("same_as")]
    for a in loose:                             # A.tar next to A.tgz
        for b in loose:
            if b is a or b.get("same_as") or a.get("same_as"):
                continue
            if a["kind"] == "tgz" and b["kind"] == "tar" \
                    and os.path.dirname(a["path"]) == os.path.dirname(b["path"]) \
                    and _stem(os.path.basename(a["path"])) == _stem(os.path.basename(b["path"])) \
                    and _gzip_isize(a["path"]) == b["size"] % (1 << 32):
                a["same_as"], a["why"] = b["path"], "the same backup, compressed (the .tar is used)"
    by_print: dict[tuple, dict] = {}
    sizes = {}
    for a in archives:
        sizes.setdefault(a["size"], []).append(a)
    # byte-identical copies (same size + first/last MiB); archives already known to be a copy
    # count as references too, so a third copy of an unpacked backup is caught
    for a in sorted(archives, key=lambda a: not a.get("same_as")):
        if len(sizes[a["size"]]) < 2:
            continue
        try:
            fp = _fingerprint(a["path"])
        except OSError:
            continue
        ref = by_print.get(fp)
        if ref is None:
            by_print[fp] = a
        elif not a.get("same_as"):
            a["same_as"], a["why"] = ref.get("same_as") or ref["path"], "identical file"
    backups, same = [], []
    imported = imported_sources(library) if library else []
    for c in cands:
        if c.get("same_as"):
            same.append({"path": c["path"], "kind": c["kind"], "same_as": c["same_as"], "why": c["why"]})
        else:
            c["when"] = _when(c["path"], c["kind"])
            c["in_library"] = _already_imported(c, imported)
            backups.append(c)
    backups.sort(key=lambda c: c["when"])
    recordings = [{"group": g, "paths": ps, "count": len(ps)} for g, ps in sorted(recs.items())]
    return {"folder": folder, "backups": backups, "same": same, "recordings": recordings,
            "unsupported": unsupported, "ignored": ignored, "ignored_bytes": ignored_bytes}


def describe_plan(plan: dict) -> str:
    """The plan as text for the terminal / the app's log."""
    top = plan["folder"]
    rel = lambda p: os.path.relpath(p, top) if p != top else "."  # noqa: E731
    new = sum(1 for c in plan["backups"] if not c.get("in_library"))
    out = [f"Found in {top}:", "", f"Backups ({len(plan['backups'])}, oldest first; {new} new, "
                                   f"{len(plan['backups']) - new} already in the library):"]
    for c in plan["backups"]:
        when = datetime.fromtimestamp(c["when"]).strftime("%Y-%m-%d")
        size = human_bytes(c["size"]) if c.get("size") else "folder"
        extra = ("  (world only)" if c.get("world_only") else "") + ("  [already in the library]" if c.get("in_library") else "")
        out.append(f"  {when}  {size:>9}  {rel(c['path'])}{extra}")
    if plan["same"]:
        out += ["", f"Copies of a backup above, skipped ({len(plan['same'])}):"]
        out += [f"  {rel(c['path'])}  ({c['why']}: {rel(c['same_as'])})" for c in plan["same"]]
    if plan["recordings"]:
        out += ["", "Recordings outside the backups:"]
        out += [f"  {rel(r['group'])}  ({r['count']} recordings)" for r in plan["recordings"]]
    if plan["unsupported"]:
        out += ["", "Can't be read (unpack them first, then import the folder):"]
        out += [f"  {rel(p)}" for p in plan["unsupported"]]
    if plan["ignored"]:
        kinds = ", ".join(f"{n} {ext}" for ext, n in sorted(plan["ignored"].items(), key=lambda kv: -kv[1])[:8])
        out += ["", f"Not backups, ignored: {kinds} ({human_bytes(plan['ignored_bytes'])})"]
    out += ["", "A backup already in the library (same time) is skipped when it is imported."]
    return "\n".join(out)


def import_all(plan: dict, library: str | None = None, mode: str = "copy", world: str | None = None,
               progress: Callable[[str], None] | None = None) -> dict:
    """Carry out a :func:`scan` plan. One failing backup doesn't stop the rest."""
    say = progress or (lambda msg: None)
    library = ensure_library(library or default_library_dir())
    done, failed, worlds = [], [], []
    for i, c in enumerate(plan["backups"], 1):
        say(f"[{i}/{len(plan['backups'])}] {c['path']}")
        if c.get("in_library"):
            say(f"  already in the library: {c['in_library']}")
            done.append({"skipped": True, "backup": c["in_library"],
                         "world": os.path.dirname(os.path.dirname(c["in_library"]))})
            worlds.append(done[-1]["world"])
            continue
        try:
            r = import_backup(c["path"], library, mode, world, say)
        except Exception as exc:  # noqa: BLE001 -- report it and go on with the next backup
            say(f"  could not import: {exc}")
            failed.append({"path": c["path"], "error": str(exc)})
            continue
        done.append(r)
        worlds.append(r["world"])
        if not r.get("skipped"):
            say(f"  -> {r['backup']}: {r['files']:,} files, {human_bytes(r['bytes'])}, "
                f"{human_bytes(r['bytes_written'])} new on disk; recordings {r['recordings_new']} new, "
                f"{r['recordings_known']} already there")
    rec_stats = []
    if plan["recordings"]:
        target = None
        if world:
            target = os.path.join(library, _safe(world))
        elif worlds:
            target = max(set(worlds), key=worlds.count)       # the world most backups went to
        else:
            ws = list_worlds(library)
            target = ws[0]["path"] if len(ws) == 1 else None
        if target and is_world_dir(target):
            for r in plan["recordings"]:
                say(f"Recordings: {r['group']}")
                st = add_recordings(r["paths"], target, mode, say, base=r["group"])
                say(f"  {st['added']} added, {st['known']} already in the library")
                rec_stats.append({"group": r["group"], **st})
        else:
            say("Recordings not added: no world to add them to (import a backup first, or give --world)")
    return {"imported": [r for r in done if not r.get("skipped")], "skipped": [r for r in done if r.get("skipped")],
            "failed": failed, "recordings": rec_stats}


# --- tidying the original folder (optional) -------------------------------------------

TIDY_UNDO = "mcsm-tidy-undo.json"
DUPLICATES_DIR = "_duplicates (check, then delete)"
_DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}")


def tidy_plan(plan: dict, rename: bool = False) -> list[dict]:
    """The moves that would tidy the scanned folder: ``[{from, to, why}]``. Nothing is deleted.

    * every copy the scan skipped goes into ``_duplicates (check, then delete)\\<same path>``;
    * with *rename*, a top-level folder holding exactly one backup gets its date in front
      (``2026-06-09 June09_2026_PostEXP2``) so the folders sort by date.
    """
    top = plan["folder"]
    moves = []
    for c in plan["same"]:
        rel = os.path.relpath(c["path"], top)
        moves.append({"from": c["path"], "to": os.path.join(top, DUPLICATES_DIR, rel),
                      "why": f"{c['why']}: {os.path.relpath(c['same_as'], top)}"})
    if rename:
        groups: dict[str, list[dict]] = {}
        for c in plan["backups"]:
            rel = os.path.relpath(c["path"], top).split(os.sep)
            if len(rel) > 1:                               # inside a top-level folder
                groups.setdefault(rel[0], []).append(c)
        for name, cs in sorted(groups.items()):
            if len(cs) != 1 or _DATE_PREFIX.match(name) or name == DUPLICATES_DIR:
                continue
            day = datetime.fromtimestamp(cs[0]["when"]).strftime("%Y-%m-%d")
            moves.append({"from": os.path.join(top, name), "to": os.path.join(top, f"{day} {name}"),
                          "why": "date in front so the folders sort by date"})
    # duplicates inside a folder that gets renamed: move them first (their paths change otherwise)
    return sorted(moves, key=lambda mv: mv["why"].startswith("date in front"))


def tidy_apply(plan: dict, moves: list[dict], progress: Callable[[str], None] | None = None) -> dict:
    """Carry out :func:`tidy_plan` moves. Each one is logged in ``mcsm-tidy-undo.json`` first,
    so :func:`tidy_undo` can put everything back. Stops at the first problem."""
    say = progress or (lambda msg: None)
    top = plan["folder"]
    log_path = os.path.join(top, TIDY_UNDO)
    log = _read_json(log_path) or {"runs": []}
    run = {"time": datetime.now(timezone.utc).isoformat(), "moves": []}
    log["runs"].append(run)
    done = 0
    for mv in moves:
        src, dst = mv["from"], mv["to"]
        if not os.path.exists(src):
            say(f"  gone already, skipped: {src}")
            continue
        if os.path.exists(dst):
            raise FileExistsError(f"{dst} already exists; nothing more was moved")
        if not _same_drive(src, top):
            raise ValueError(f"{src} is on another drive; tidy only moves within one drive")
        run["moves"].append({"from": src, "to": dst})
        _write_json(log_path, log)                     # logged before it happens
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        os.replace(src, dst)                           # same drive: instant, nothing copied
        done += 1
        say(f"  moved {os.path.relpath(src, top)}  ->  {os.path.relpath(dst, top)}")
    return {"moved": done, "undo_file": log_path}


def tidy_undo(folder: str, progress: Callable[[str], None] | None = None) -> dict:
    """Put back everything the last tidy moved (newest move first)."""
    say = progress or (lambda msg: None)
    log_path = os.path.join(folder, TIDY_UNDO)
    log = _read_json(log_path)
    if not log.get("runs"):
        raise ValueError(f"nothing to undo: {log_path} not found")
    run = log["runs"][-1]
    back = 0
    for mv in reversed(run["moves"]):
        if os.path.exists(mv["to"]) and not os.path.exists(mv["from"]):
            os.makedirs(os.path.dirname(mv["from"]), exist_ok=True)
            os.replace(mv["to"], mv["from"])
            back += 1
            say(f"  put back {mv['from']}")
        else:
            say(f"  can't put back {mv['from']} (moved or changed since)")
    log["runs"].pop()
    if log["runs"]:
        _write_json(log_path, log)
    else:
        os.remove(log_path)
    dup = os.path.join(folder, DUPLICATES_DIR)
    for cur, _dirs, _files in os.walk(dup, topdown=False) if os.path.isdir(dup) else []:   # children first
        try:
            os.rmdir(cur)                              # only empty folders
        except OSError:
            pass
    return {"put_back": back}


def describe_tidy(plan: dict, moves: list[dict]) -> str:
    top = plan["folder"]
    if not moves:
        return "Nothing to tidy."
    out = [f"Tidy {top} ({len(moves)} moves, nothing is deleted, all can be undone):"]
    for mv in moves:
        out.append(f"  {os.path.relpath(mv['from'], top)}\n     -> {os.path.relpath(mv['to'], top)}   ({mv['why']})")
    return "\n".join(out)


# --- video projects --------------------------------------------------------------------

PROJECT_MARKER = "project.json"


def _replay_uuid(path: str) -> tuple[str | None, str]:
    """``(uuid, minecraft version)`` from a Flashback replay's metadata."""
    try:
        if os.path.isdir(path):
            with open(os.path.join(path, "metadata.json"), encoding="utf-8") as fh:
                meta = json.load(fh)
        else:
            with zipfile.ZipFile(_lp(path)) as zf:
                meta = json.loads(zf.read("metadata.json").decode("utf-8", "replace"))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return None, ""
    return (str(meta["uuid"]) if meta.get("uuid") else None), str(meta.get("version_string") or "")


def _recordings_in(folder: str, recursive: bool = True) -> list[str]:
    """Recordings in *folder*, recognised by their contents (whatever the folder is called)."""
    from .replays import _zip_kind
    out = []
    for cur, dirs, files in os.walk(folder):
        keep = []
        for d in sorted(dirs):
            full = os.path.join(cur, d)
            if _is_flashback_dir(full) or _is_replaymod_dir(full):
                out.append(full)
            else:
                keep.append(d)
        dirs[:] = keep if recursive else []
        for f in sorted(files):
            full = os.path.join(cur, f)
            if f.lower().endswith(".mcpr") or (f.lower().endswith(".zip") and _zip_kind(full)):
                out.append(full)
    return out


def add_projects(folder: str, world_dir: str, progress: Callable[[str], None] | None = None) -> dict:
    """Add video projects: each subfolder of *folder* is one project (``EXP2 - Fishing``); its
    recordings, maybe in per-person subfolders (``Daragh``, ``Me``), are the footage it uses.

    The recordings are added to the world (once; a copy already there is reused). Each
    recording's Flashback editor state (the edits) is found in any Flashback folder (launcher
    instances included) and **copied** into ``projects/<project>/editor_states/`` as a backup;
    the launcher's files are never changed. ``projects/<project>/project.json`` lists it all.
    """
    from .flashback import find_editor_state
    say = progress or (lambda msg: None)
    if not is_world_dir(world_dir):
        raise ValueError(f"{world_dir} is not a library world folder")
    folder = os.path.abspath(folder)
    subs = [os.path.join(folder, d) for d in sorted(os.listdir(folder))
            if os.path.isdir(os.path.join(folder, d)) and not d.startswith(".")]
    loose = [p for p in os.listdir(folder) if p.lower().endswith((".zip", ".mcpr"))]
    projects = ([folder] if loose else []) + subs
    rec_root = os.path.join(world_dir, "recordings")
    out = {"projects": [], "recordings_added": 0, "editor_states": 0, "missing_edits": []}
    for proj in projects:
        found = _recordings_in(proj, recursive=proj != folder)
        if not found:
            continue
        name = os.path.basename(proj)
        pdir = os.path.join(world_dir, "projects", _safe(name))
        os.makedirs(os.path.join(pdir, "editor_states"), exist_ok=True)
        entries = []
        for p in found:
            index = _recording_index(rec_root)
            lib_path = _find_twin(index, p, _size(p)) if os.path.isfile(p) else None
            if lib_path is None:
                st = add_recordings([p], world_dir, "copy", base=folder)
                out["recordings_added"] += st["added"]
                index = _recording_index(rec_root)
                lib_path = _find_twin(index, p, _size(p)) if os.path.isfile(p) else None
            uuid, mc = _replay_uuid(p)
            entry = {"original": p, "file": os.path.relpath(lib_path, world_dir) if lib_path else None,
                     "perspective": os.path.relpath(os.path.dirname(p), proj) if os.path.dirname(p) != proj else "",
                     "uuid": uuid, "minecraft": mc, "editor_state": None, "editor_state_from": None}
            state = find_editor_state(uuid) if uuid else None
            if state:
                for src in (state, state + ".old"):
                    if os.path.isfile(src):
                        dst = os.path.join(pdir, "editor_states", os.path.basename(src))
                        if not (os.path.isfile(dst) and _identical(src, dst)):
                            _new_file(dst, lambda part, src=src: shutil.copy2(src, part))
                entry["editor_state"] = os.path.relpath(os.path.join(pdir, "editor_states", f"{uuid}.json"), world_dir)
                entry["editor_state_from"] = state
                out["editor_states"] += 1
            else:
                out["missing_edits"].append(p)
            entries.append(entry)
            who = f" ({entry['perspective']})" if entry["perspective"] and entry["perspective"] != "." else ""
            say(f"  {name}{who}: {os.path.basename(p)}" + ("  + edits" if state else "  (no Flashback edits found)"))
        meta = _read_json(os.path.join(pdir, PROJECT_MARKER))
        _write_json(os.path.join(pdir, PROJECT_MARKER), {
            "name": name, "source": proj, "added": meta.get("added") or datetime.now(timezone.utc).isoformat(),
            "updated": datetime.now(timezone.utc).isoformat(), "recordings": entries,
            "notes": meta.get("notes", ""), "status": meta.get("status", "editing")})
        out["projects"].append({"name": name, "path": pdir, "recordings": len(entries)})
    return out


def list_projects(world_dir: str) -> list[dict]:
    root = os.path.join(world_dir, "projects")
    out = []
    for name in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        meta = _read_json(os.path.join(root, name, PROJECT_MARKER))
        if meta:
            out.append({**meta, "path": os.path.join(root, name)})
    return out


def human_bytes(n: int | float | None) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
