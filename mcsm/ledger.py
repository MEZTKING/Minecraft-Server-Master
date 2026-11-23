"""The Ledger mod's log of block changes (``<world>/ledger.sqlite``), for blocks as they were at any moment.

Ledger records every block broken, placed or changed on the server (by players, explosions, fire, decay,
fluids...) with its time (UTC), position, world, and the block before and after. With a backup saved at time
*B*, the world at an earlier time *T* is the backup with every change between *T* and *B* undone: at each
position the earliest change after *T* says what was there at *T* (its "old" block). For *T* after *B* the
changes from *B* to *T* are applied instead (the latest one at each position wins).

The database is only read (``mode=ro&immutable=1``), never changed. Rows rolled back with ``/ledger rollback``
are left out (the rollback put the old block back).
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
from datetime import datetime, timezone

BLOCK_ACTIONS = ("block-break", "block-place", "block-change")
_PROPS = re.compile(r'(\w+):"?([^",}]*)"?')


def find(root: str | None, world: str | None) -> str | None:
    """The Ledger database of a server folder, if it has one."""
    if not root:
        return None
    for p in ([os.path.join(root, world, "ledger.sqlite")] if world else []) + [os.path.join(root, "ledger.sqlite")]:
        if os.path.isfile(p):
            return p
    return None


def state(identifier: str, props: str | None) -> str:
    """``minecraft:oak_stairs`` + ``{facing:"east",half:"top"}`` -> ``minecraft:oak_stairs[facing=east,half=top]``."""
    if not props:
        return identifier
    pairs = _PROPS.findall(props)
    return identifier + ("[" + ",".join(f"{k}={v}" for k, v in pairs) + "]" if pairs else "")


def _when(ms: int) -> str:
    """Ledger's time text for epoch milliseconds (UTC, sortable as text)."""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _ms(text: str) -> int:
    t = datetime.strptime(text[:23].ljust(23, "0") if "." in text else text + ".000", "%Y-%m-%d %H:%M:%S.%f")
    return int(t.replace(tzinfo=timezone.utc).timestamp() * 1000)


class Ledger:
    """One Ledger database, opened read-only (one connection per thread)."""

    def __init__(self, path: str):
        self.path = path
        self._local = threading.local()
        con = self._con()
        self.actions = {name: i for i, name in con.execute("select id, action_identifier from ActionIdentifiers")}
        self.worlds = {name: i for i, name in con.execute("select id, identifier from worlds")}
        self.objects = dict(con.execute("select id, identifier from ObjectIdentifiers"))
        self.block_ids = tuple(self.actions[a] for a in BLOCK_ACTIONS if a in self.actions)
        r = con.execute("select min(time), max(time) from actions").fetchone()
        self.first, self.last = (_ms(r[0]), _ms(r[1])) if r and r[0] else (None, None)

    def _con(self) -> sqlite3.Connection:
        con = getattr(self._local, "con", None)
        if con is None:
            uri = "file:" + self.path.replace("\\", "/").replace("?", "%3f").replace("#", "%23") + "?mode=ro&immutable=1"
            con = self._local.con = sqlite3.connect(uri, uri=True, check_same_thread=False)
        return con

    def changes(self, dim: str, x0: int, z0: int, x1: int, z1: int, t0: int, t1: int) -> list[tuple]:
        """Block changes in the box (x0..x1, z0..z1 inclusive) from *t0* to *t1* (ms, both included), in time order:
        ``(ms, x, y, z, new state, old state)``."""
        w = self.worlds.get(dim)
        if w is None or not self.block_ids:
            return []
        q = ("select time, x, y, z, object_id, block_state, old_object_id, old_block_state from actions "
             f"where world_id = ? and action_id in ({','.join('?' * len(self.block_ids))}) and rolled_back = 0 "
             "and x between ? and ? and z between ? and ? and time between ? and ? order by time, id")
        rows = self._con().execute(q, (w, *self.block_ids, x0, x1, z0, z1, _when(t0), _when(t1))).fetchall()
        obj = self.objects
        return [(_ms(t), x, y, z, state(obj.get(o, "minecraft:air"), s), state(obj.get(oo, "minecraft:air"), os_))
                for t, x, y, z, o, s, oo, os_ in rows]

    def patches(self, dim: str, chunks: set[tuple[int, int]], at: int, base: int) -> dict[tuple[int, int], dict]:
        """The blocks that differ from a backup saved at *base* (ms) at the moment *at*, per chunk:
        ``{(cx, cz): {(x & 15, y, z & 15): state}}``."""
        if not chunks:
            return {}
        xs, zs = [c[0] for c in chunks], [c[1] for c in chunks]
        lo, hi = (at + 1, base) if at < base else (base + 1, at)
        rows = self.changes(dim, min(xs) * 16, min(zs) * 16, max(xs) * 16 + 15, max(zs) * 16 + 15, lo, hi)
        found: dict[tuple[int, int, int], str] = {}
        if at < base:                                   # undo: the first change after *at* says what was there
            for _t, x, y, z, _new, old in rows:
                found.setdefault((x, y, z), old)
        else:                                           # replay: the last change up to *at* wins
            for _t, x, y, z, new, _old in rows:
                found[(x, y, z)] = new
        out: dict[tuple[int, int], dict] = {}
        for (x, y, z), s in found.items():
            c = (x >> 4, z >> 4)
            if c in chunks:
                out.setdefault(c, {})[(x & 15, y, z & 15)] = s
        return out


_OPEN: dict[str, Ledger | None] = {}


def open_ledger(path: str | None) -> Ledger | None:
    """A Ledger database, opened once (None when there is none or it can't be read)."""
    if not path:
        return None
    if path not in _OPEN:
        try:
            _OPEN[path] = Ledger(path)
        except (sqlite3.Error, OSError, ValueError):
            _OPEN[path] = None
    return _OPEN[path]
