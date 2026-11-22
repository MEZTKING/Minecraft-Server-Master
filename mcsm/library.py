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

def _has_server_files(d: str) -> bool:
    return any(os.path.exists(os.path.join(d, m)) for m in ("server.properties", "logs", "usercache.json"))


def _is_world_folder(d: str) -> bool:
    return os.path.isfile(os.path.join(d, "level.dat")) and (
        os.path.isdir(os.path.join(d, "region")) or os.path.isdir(os.path.join(d, "dimensions"))
        or os.path.isdir(os.path.join(d, "playerdata")) or os.path.isdir(os.path.join(d, "players")))


def locate(path: str) -> tuple[str, bool]:
    """``(root, world_only)`` for an unpacked backup: the server root, or -- for a backup that
    holds only a world (Aternos/Realms downloads, singleplayer saves) -- the world folder."""
    root = find_server_root(path)
    if _has_server_files(root):
        return root, False
    for cur, dirs, _files in os.walk(path):
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
    times = []
    lp = lvl.get("last_played_ms")
    if isinstance(lp, (int, float)) and lp > 0:
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


def _same_folder(a: str, b: str) -> bool:
    """Two recording folders with the same files and identical contents."""
    fa = sorted(os.path.relpath(f, a) for f in _walk_files(a))
    fb = sorted(os.path.relpath(f, b) for f in _walk_files(b))
    return fa == fb and all(_identical(os.path.join(a, f), os.path.join(b, f)) for f in fa)


def _find_folder_twin(rec_root: str, folder: str) -> str | None:
    name = os.path.basename(folder.rstrip("/" + chr(92)))
    for cur, dirs, _files in os.walk(rec_root):
        if name in dirs and _same_folder(folder, os.path.join(cur, name)):
            return os.path.join(cur, name)
    return None


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
                 say: Callable[[str], None], force: bool = False, world_only: bool = False) -> dict:
    info = _probe(root, world_only)
    # A world-only backup becomes <backup>/<world folder>/ plus a generated server.properties.
    prefix = _safe(os.path.basename(os.path.normpath(root))) + "/" if world_only else ""
    w = _find_or_make_world(library, info, world_name)
    when = info["snapshot_ms"]
    stamp = datetime.fromtimestamp(when / 1000).strftime("%Y-%m-%d %H-%M") if when else "undated"
    for b in world_backups(w):
        # same moment = same backup (2 s tolerance: .zip stores times to 2 seconds)
        if when and b.get("snapshot_ms") and abs(b["snapshot_ms"] - when) <= 2000 and not force:
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
        r = in_replay(rel)
        rel = prefix + rel
        size = os.path.getsize(p)
        stats["files"] += 1
        stats["bytes"] += size
        if r is not None:                                   # a recording: store once per world
            rel = rel[len(prefix):]
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
    if world_only:
        with open(os.path.join(dest, "server.properties"), "w", encoding="utf-8") as fh:
            fh.write("# written by mcsm: this backup contained only the world folder\n"
                     f"level-name={prefix.rstrip('/')}\n")
    meta = {"world_only": world_only, "source": os.path.abspath(source), "imported": datetime.now(timezone.utc).isoformat(),
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
        root, world_only = locate(source)
        if os.path.normcase(os.path.abspath(root)).startswith(os.path.normcase(library) + os.sep):
            raise ValueError(f"{source} is already inside the library")
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
        return _import_root(root, library, "move", world, source, say, force, world_only)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        try:
            os.rmdir(staging_parent)
        except OSError:
            pass


def add_recordings(paths: list[str], world_dir: str, mode: str = "auto",
                   progress: Callable[[str], None] | None = None, base: str | None = None) -> dict:
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
        elif base:                                  # keep the layout below *base*
            found, src = [src], os.path.abspath(base)
            base_dir = os.path.join(dest_root, _safe(os.path.basename(src.rstrip("/" + chr(92)))))
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
            elif os.path.exists(target) or _find_folder_twin(os.path.join(world_dir, "recordings"), p):
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
        if size > 2 << 20:
            fh.seek(-(1 << 20), os.SEEK_END)
            h.update(fh.read(1 << 20))
    return size, h.hexdigest()


def _when(path: str, kind: str) -> float:
    if kind == "folder":
        for rel in ("logs/latest.log", "level.dat"):
            p = os.path.join(path, *rel.split("/"))
            if os.path.isfile(p):
                return os.path.getmtime(p)
    return os.path.getmtime(path)


def _is_server_dir(d: str) -> bool:
    return _has_server_files(d) and (os.path.isfile(os.path.join(d, "server.properties"))
                                     or os.path.isdir(os.path.join(d, "logs")))


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
                    a["same_as"], a["why"] = f["path"], "unpacked copy next to it"
                    break
                d = os.path.dirname(d)
            if a.get("same_as"):
                break
    loose = [a for a in archives if not a.get("same_as")]
    for a in loose:                             # A.tar next to A.tgz
        for b in loose:
            if b is a or b.get("same_as") or a.get("same_as"):
                continue
            if os.path.dirname(a["path"]) == os.path.dirname(b["path"]) \
                    and _stem(os.path.basename(a["path"])) == _stem(os.path.basename(b["path"])) \
                    and _PREFER[a["kind"]] > _PREFER[b["kind"]]:
                a["same_as"], a["why"] = b["path"], f"same backup as the .{b['kind']}"
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
    for c in cands:
        if c.get("same_as"):
            same.append({"path": c["path"], "kind": c["kind"], "same_as": c["same_as"], "why": c["why"]})
        else:
            c["when"] = _when(c["path"], c["kind"])
            backups.append(c)
    backups.sort(key=lambda c: c["when"])
    recordings = [{"group": g, "paths": ps, "count": len(ps)} for g, ps in sorted(recs.items())]
    return {"folder": folder, "backups": backups, "same": same, "recordings": recordings,
            "unsupported": unsupported, "ignored": ignored, "ignored_bytes": ignored_bytes}


def describe_plan(plan: dict) -> str:
    """The plan as text for the terminal / the app's log."""
    top = plan["folder"]
    rel = lambda p: os.path.relpath(p, top) if p != top else "."  # noqa: E731
    out = [f"Found in {top}:", "", f"Backups to import ({len(plan['backups'])}, oldest first):"]
    for c in plan["backups"]:
        when = datetime.fromtimestamp(c["when"]).strftime("%Y-%m-%d")
        size = human_bytes(c["size"]) if c.get("size") else "folder"
        extra = "  (world only)" if c.get("world_only") else ""
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
        try:
            r = import_backup(c["path"], library, mode, world, say)
        except (OSError, ValueError, tarfile.TarError, zipfile.BadZipFile, EOFError) as exc:
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


def human_bytes(n: int | float | None) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
