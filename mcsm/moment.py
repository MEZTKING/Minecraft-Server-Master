"""Blocks as they were at a moment, from Flashback recordings.

A recording holds what its player's game received. Chunk data (``level_chunk_with_light``: every block, biome and
light value of a chunk) is kept once in the zip's ``level_chunk_caches/<n>`` members (10,000 packets each: a 4-byte
length, then the game packet), referred to by ``level_chunk_cached`` actions (the entry's number). Each part of a
recording (``c<n>.flashback``, usually 5 minutes) starts with a snapshot that loads every chunk the game had loaded
then, followed by the stream: new chunks as the player moves and block changes (``block_update``,
``section_blocks_update``). So the world a recording saw at tick *t* is its part's snapshot plus the stream up to
*t*. Flashback keeps no light updates and no chunk unloads.

Several recordings see the same chunks; all of them are the server's truth, so for each chunk the most recently
loaded copy is the base and every recording's later changes are applied in world-clock order. Chunks no recording
had loaded keep the backup's blocks.

:class:`RecChunk` has the interface of :class:`mcsm.blockmesh.Chunk` (``state``, ``biome``, ``light``, ``tops``,
``min_y``), so :func:`mcsm.blockmesh.build` meshes it like a backup chunk.
"""
from __future__ import annotations

import json
import math
import os
import struct
import zipfile

from . import paths as paths_mod
from . import protocol

ENTRIES_PER_CACHE = 10_000
_U32 = struct.Struct(">I")
_Q = struct.Struct(">Q")
_vi = paths_mod._varint
MOTION_BLOCKING = 4                    # the network heightmap type for the game's MOTION_BLOCKING
AIRS = {"minecraft:air", "minecraft:cave_air", "minecraft:void_air"}


def _varlong(b, k: int) -> tuple[int, int]:
    v, shift = 0, 0
    while True:
        x = b[k]
        k += 1
        v |= (x & 0x7F) << shift
        if not x & 0x80:
            return v, k
        shift += 7


def _signed(v: int, bits: int) -> int:
    return v - (1 << bits) if v >= 1 << (bits - 1) else v


def block_pos(v: int) -> tuple[int, int, int]:
    """A packed block position (x 26 bits, z 26 bits, y 12 bits)."""
    v &= (1 << 64) - 1
    return _signed(v >> 38, 26), _signed(v & 0xFFF, 12), _signed((v >> 12) & 0x3FFFFFF, 26)


def section_pos(v: int) -> tuple[int, int, int]:
    """A packed section position (x 22 bits, z 22 bits, y 20 bits)."""
    v &= (1 << 64) - 1
    return _signed(v >> 42, 22), _signed(v & 0xFFFFF, 20), _signed((v >> 20) & 0x3FFFFF, 22)


def min_y_of(dim: str | None) -> int:
    return -64 if (dim or "minecraft:overworld") == "minecraft:overworld" else 0


# ---------------------------------------------------------------- chunk packets
class _Container:
    """A network paletted container: palette (None = global ids), packed longs, bits per entry."""
    __slots__ = ("pal", "data", "off", "bits", "per")

    def __init__(self, pal, data, off, bits):
        self.pal, self.data, self.off, self.bits = pal, data, off, bits
        self.per = 64 // bits if bits else 0

    def get(self, i: int) -> int:
        if not self.bits:
            return self.pal[0]
        word = _Q.unpack_from(self.data, self.off + 8 * (i // self.per))[0]
        v = (word >> ((i % self.per) * self.bits)) & ((1 << self.bits) - 1)
        if self.pal is None:
            return v
        return self.pal[v] if v < len(self.pal) else self.pal[0]


def _container(b, k: int, entries: int, small: int, direct_bits: int, min_bits: int) -> tuple[_Container, int]:
    bits = b[k]
    k += 1
    if bits == 0:
        v, k = _vi(b, k)
        return _Container([v], b, k, 0), k
    if bits <= small:
        n, k = _vi(b, k)
        pal = []
        for _ in range(n):
            v, k = _vi(b, k)
            pal.append(v)
        bits = max(min_bits, bits)
    else:
        pal, bits = None, direct_bits
    longs = math.ceil(entries / (64 // bits))
    return _Container(pal, b, k, bits), k + 8 * longs


def _light(packet: bytes, k: int, as_bytes: bool):
    try:
        masks = []
        for _ in range(4):
            n, k = _vi(packet, k)
            if as_bytes:
                masks.append(int.from_bytes(packet[k:k + n], "little"))
                k += n
            else:
                masks.append(sum(_Q.unpack_from(packet, k + 8 * i)[0] << (64 * i) for i in range(n)))
                k += 8 * n
        out = ({}, {})
        for which, mask in zip(out, masks[:2]):
            n, k = _vi(packet, k)
            bit = 0
            for _ in range(n):
                while not (mask >> bit) & 1:
                    bit += 1
                    if bit > 4096:
                        return None
                ln, k = _vi(packet, k)
                if ln != 2048:
                    return None
                which[bit - 1] = packet[k:k + ln]            # keyed by section index (0 = the lowest)
                k += ln
                bit += 1
        return out if k == len(packet) else None
    except (IndexError, struct.error):
        return None


class RecChunk:
    """One chunk as a recording received it (plus later block changes)."""

    def __init__(self, packet: bytes, proto: int, states: list[str], biomes: list[str], dim: str | None):
        self.states, self.biome_names = states, biomes
        self.min_y = min_y_of(dim)
        k = 0
        _pid, k = _vi(packet, k)
        self.cx, self.cz = struct.unpack_from(">ii", packet, k)
        k += 8
        tops = None
        n, k = _vi(packet, k)
        for _ in range(n):
            t, k = _vi(packet, k)
            m, k = _vi(packet, k)
            if t == MOTION_BLOCKING and m:
                bits = next((bb for bb in range(1, 17) if math.ceil(256 / (64 // bb)) == m), 9)
                per = 64 // bits
                tops = [((_Q.unpack_from(packet, k + 8 * (i // per))[0] >> ((i % per) * bits)) & ((1 << bits) - 1))
                        + self.min_y - 1 for i in range(256)]
            k += 8 * m
        size, k = _vi(packet, k)
        data_end = k + size
        two_counts = proto >= 775                      # 26.1+: block count and fluid count
        state_bits = max(1, math.ceil(math.log2(max(2, len(states)))))
        biome_bits = max(1, math.ceil(math.log2(max(2, len(biomes)))))
        self.sections: list[tuple[_Container, _Container]] = []
        while k < data_end:
            k += 4 if two_counts else 2
            blocks, k = _container(packet, k, 4096, 8, state_bits, 4)
            bios, k = _container(packet, k, 64, 3, biome_bits, 1)
            self.sections.append((blocks, bios))
        k = data_end
        n, k = _vi(packet, k)                          # block entities: not drawn
        for _ in range(n):
            k += 3
            _t, k = _vi(packet, k)
            k = paths_mod._nbt_skip(packet, k)
        # light: masks of sections with data (bit 0 = the section below the lowest), then the arrays. The masks
        # are longs in some versions and bytes in others (26.3): whichever reads to the packet's end exactly
        self.sky: dict[int, bytes] = {}
        self.block: dict[int, bytes] = {}
        for as_bytes in (True, False):
            got = _light(packet, k, as_bytes)
            if got is not None:
                self.sky, self.block = got
                break
        self.lit = bool(self.sky or self.block)
        if tops is None:
            tops = [self._scan_top(x, z, self.min_y + 16 * len(self.sections) - 1) for z in range(16) for x in range(16)]
        self.tops = tops
        self.edits: dict[tuple[int, int, int], int] = {}
        self.backup = None                              # the backup's chunk here (unused for now)

    # -- blocks
    def _id(self, x: int, y: int, z: int) -> int | None:
        e = self.edits.get((x, y, z))
        if e is not None:
            return e
        si = (y - self.min_y) >> 4
        if not 0 <= si < len(self.sections):
            return None
        return self.sections[si][0].get(((y & 15) << 8) | (z << 4) | x)

    def state(self, x: int, y: int, z: int) -> str | None:
        i = self._id(x, y, z)
        return self.states[i] if i is not None and 0 <= i < len(self.states) else None

    def _scan_top(self, x: int, z: int, start: int) -> int:
        for y in range(start, self.min_y - 1, -1):
            s = self.state(x, y, z)
            if s and s.partition("[")[0] not in AIRS:
                return y
        return self.min_y - 1

    def apply(self, x: int, y: int, z: int, state_id: int) -> None:
        """A block change (chunk-local x, z)."""
        self.edits[(x, y, z)] = state_id
        i = z * 16 + x
        s = self.states[state_id] if 0 <= state_id < len(self.states) else "minecraft:air"
        if s.partition("[")[0] not in AIRS:
            self.tops[i] = max(self.tops[i], y)
        elif y >= self.tops[i]:
            self.tops[i] = self._scan_top(x, z, y)

    # -- biomes and light
    def biome(self, x: int, y: int, z: int) -> str | None:
        si = (y - self.min_y) >> 4
        if not 0 <= si < len(self.sections):
            return None
        v = self.sections[si][1].get((((y & 15) >> 2) << 4) | ((z >> 2) << 2) | (x >> 2))
        return self.biome_names[v] if 0 <= v < len(self.biome_names) else None

    def _stored(self, arrays: dict, x: int, y: int, z: int) -> int | None:
        a = arrays.get((y - self.min_y) >> 4)
        if not a:
            return None
        i = ((y & 15) << 8) | (z << 4) | x
        return (a[i >> 1] >> (4 * (i & 1))) & 15

    def light(self, x: int, y: int, z: int) -> tuple[int, int]:
        """(sky, block) light of the space at x, y, z. Where a block was changed (no light updates are
        recorded), the light is worked out from the spaces around it."""
        sky = self._stored(self.sky, x, y, z)
        blk = self._stored(self.block, x, y, z)
        if sky is None:
            sky = 15 if y > self.tops[z * 16 + x] else 0
        if blk is None:
            blk = 0
        if self.edits and (x, y, z) in self.edits:
            if y > self.tops[z * 16 + x]:
                sky = 15
            else:
                around = [(x + dx, y + dy, z + dz) for dx, dy, dz in
                          ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))
                          if 0 <= x + dx < 16 and 0 <= z + dz < 16]
                sky = max([sky] + [(self._stored(self.sky, *p) or 0) - 1 for p in around])
                blk = max([blk] + [(self._stored(self.block, *p) or 0) - 1 for p in around])
        return max(0, sky), max(0, blk)


# ---------------------------------------------------------------- the chunk caches
class CacheIndex:
    """Where each chunk packet is inside a recording's ``level_chunk_caches/<n>`` members, and which chunk it is
    (found once by skimming each member, kept on disk)."""

    VERSION = 2

    def __init__(self, zip_path: str, cache_dir: str | None):
        self.zip_path = zip_path
        st = os.stat(zip_path)
        key = paths_mod._cache_key(zip_path, None)
        self.file = os.path.join(cache_dir, f"{key}.chunkidx.json") if cache_dir else None
        self.members: dict[str, list[list[int]]] = {}  # member -> [[offset, cx, cz], ...]
        self.stamp = [st.st_size, int(st.st_mtime), self.VERSION]
        self.changed = False
        if self.file and os.path.isfile(self.file):
            try:
                with open(self.file, encoding="utf-8") as fh:
                    d = json.load(fh)
                if d.get("stamp") == self.stamp:
                    self.members = d.get("members") or {}
            except (OSError, ValueError):
                pass

    def _scan(self, z: zipfile.ZipFile, member: str) -> list[list[int]]:
        out = []
        pos = 0
        with z.open(member) as fh:
            while True:
                h = fh.read(4)
                if len(h) < 4:
                    break
                ln = _U32.unpack(h)[0]
                head = fh.read(min(ln, 13))
                try:
                    cx, cz = chunk_xz(head)
                except (IndexError, struct.error):
                    cx = cz = 0
                out.append([pos, cx, cz])
                fh.seek(ln - len(head), 1)
                pos += 4 + ln
        return out

    def _member(self, z: zipfile.ZipFile, f: int) -> list[list[int]]:
        member = f"level_chunk_caches/{f}"
        if member not in self.members:
            try:
                self.members[member] = self._scan(z, member)
            except (KeyError, OSError, zipfile.BadZipFile, struct.error):
                self.members[member] = []
            self.changed = True
        return self.members[member]

    def where(self, z: zipfile.ZipFile, entry: int) -> tuple[int, int] | None:
        """The chunk (cx, cz) of a packet, from the index."""
        m = self._member(z, entry // ENTRIES_PER_CACHE)
        j = entry % ENTRIES_PER_CACHE
        return (m[j][1], m[j][2]) if j < len(m) else None

    def read(self, z: zipfile.ZipFile, indexes: list[int]) -> dict[int, bytes]:
        """The chunk packets with those entry numbers."""
        out: dict[int, bytes] = {}
        by_file: dict[int, list[int]] = {}
        for i in set(indexes):
            by_file.setdefault(i // ENTRIES_PER_CACHE, []).append(i)
        for f, idx in sorted(by_file.items()):
            offs = self._member(z, f)
            try:
                with z.open(f"level_chunk_caches/{f}") as fh:
                    for i in sorted(idx):
                        j = i % ENTRIES_PER_CACHE
                        if j >= len(offs):
                            continue
                        fh.seek(offs[j][0])
                        ln = _U32.unpack(fh.read(4))[0]
                        out[i] = fh.read(ln)
            except (KeyError, OSError, zipfile.BadZipFile, struct.error):
                continue
        self.save()
        return out

    def save(self) -> None:
        if not (self.changed and self.file):
            return
        try:
            os.makedirs(os.path.dirname(self.file), exist_ok=True)
            with open(self.file + ".part", "w", encoding="utf-8") as fh:
                json.dump({"stamp": self.stamp, "members": self.members}, fh, separators=(",", ":"))
            os.replace(self.file + ".part", self.file)
            self.changed = False
        except OSError:
            pass


# ---------------------------------------------------------------- one recording at one tick
def _biome_names(config_packets: list[bytes]) -> list[str]:
    """The biome registry (names in network id order) from the recording's configuration packets."""
    for p in config_packets:
        names = paths_mod.registry_names(p, "minecraft:worldgen/biome")
        if names is not None:
            return names
    return []


_PARTS: dict = {}                                     # (zip, part) -> everything in it (the last few dozen)


def recording_state(zip_path: str, tick: int) -> dict | None:
    """What one recording's game had loaded at *tick*: ``{"proto", "version", "biomes", "loads": [[dimension, load
    tick, cache entry], ...], "edits": [[tick, dimension, x, y, z, state id], ...]}`` (in order)."""
    try:
        with zipfile.ZipFile(zip_path) as z:
            meta = json.loads(z.read("metadata.json"))
            names = paths_mod._chunk_names(z)
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return None
    durations = meta.get("chunks") or {}
    start, part, part_start = 0, None, 0              # the part that holds *tick*
    for n in names:
        d = int((durations.get(n) or {}).get("duration") or 6000)
        part, part_start = n, start
        if tick < start + d:
            break
        start += d
    if part is None:
        return None
    key = (zip_path, part)
    data = _PARTS.get(key) or _remember(_PARTS, key, _read_part(zip_path, meta, part, part_start), 48)
    if data is None:
        return None
    since = max([r for r in data["respawns"] if r <= tick], default=-1)   # a new dimension: everything unloaded
    return {"proto": data["proto"], "version": data["version"], "biomes": data["biomes"], "part": part,
            "loads": [ld for ld in data["loads"] if since <= ld[1] <= tick and ld[0] is not None],
            "edits": [e for e in data["edits"] if since <= e[0] <= tick and e[1] is not None]}


def _read_part(zip_path: str, meta: dict, part: str, start: int) -> dict | None:
    """Every chunk load, block change and change of dimension in one part of a recording."""
    proto = meta.get("protocol_version")
    tbl = protocol.table(proto, meta.get("version_string"))
    pk = (tbl or {}).get("packets") or {}
    bu, sbu, resp = pk.get("block_update"), pk.get("section_blocks_update"), pk.get("respawn")
    if bu is None or sbu is None:
        return None
    try:
        with zipfile.ZipFile(zip_path) as z:
            b = z.read(part)
    except (OSError, KeyError, zipfile.BadZipFile):
        return None
    i = 4
    count, i = _vi(b, i)
    kinds = []
    for _ in range(count):
        ln, i = _vi(b, i)
        kinds.append(b[i:i + ln].decode().rsplit("/", 1)[-1])
        i += ln
    i += 4                                              # the snapshot's length: read on, it comes first
    k_of = {k: n for n, k in enumerate(kinds)}
    nt, gp, mv = k_of.get("next_tick"), k_of.get("game_packet"), k_of.get("move_entities")
    lc, cfg = k_of.get("level_chunk_cached"), k_of.get("configuration_packet")
    loads: list[list] = []                              # [dimension, tick, cache entry]
    edits: list[list] = []
    unknown: list[list] = []                            # loads and edits before their dimension is known
    respawns: list[int] = []
    configs: list[bytes] = []
    dim = None
    t = start
    end = len(b)
    j = i
    while j + 5 <= end:
        aid, j = _vi(b, j)
        size = _U32.unpack_from(b, j)[0]
        j += 4
        body_end = j + size
        if aid == nt:
            t += 1
        elif aid == lc:
            ld = [dim, t, _vi(b, j)[0]]
            loads.append(ld)
            if dim is None:
                unknown.append(ld)
        elif aid == cfg:
            configs.append(bytes(b[j:body_end]))
        elif aid == mv and size > 2:
            try:
                _lv, k = _vi(b, j)
                ln, k = _vi(b, k)
                d = b[k:k + ln].decode("utf-8", "replace")
                if d:
                    dim = d
                    for item in unknown:
                        item[0 if len(item) == 3 else 1] = d
                    unknown.clear()
            except (IndexError, UnicodeDecodeError):
                pass
        elif aid == gp:
            pid, k = _vi(b, j)
            new = []
            if pid == bu:
                pos, k = struct.unpack_from(">q", b, k)[0], k + 8
                sid, k = _vi(b, k)
                x, y, zz = block_pos(pos)
                new.append([t, dim, x, y, zz, sid])
            elif pid == sbu:
                sp, k = struct.unpack_from(">q", b, k)[0], k + 8
                sx, sy, sz = section_pos(sp)
                n, k = _vi(b, k)
                for _ in range(n):
                    v, k = _varlong(b, k)
                    new.append([t, dim, sx * 16 + ((v >> 8) & 15), sy * 16 + (v & 15), sz * 16 + ((v >> 4) & 15), v >> 12])
            elif pid == resp:
                respawns.append(t)
                dim = None
            edits += new
            if dim is None:
                unknown += new
        j = body_end
    return {"proto": proto, "version": meta.get("version_string"), "biomes": _biome_names(configs),
            "loads": loads, "edits": edits, "respawns": respawns}


def chunk_xz(packet: bytes) -> tuple[int, int]:
    k = _vi(packet, 0)[1]
    return struct.unpack_from(">ii", packet, k)


# ---------------------------------------------------------------- the world at a moment, from several recordings
_STATES: dict = {}                                    # (zip, tick) -> recording_state (the last few)
_PACKETS: dict = {}                                   # (zip, entry) -> chunk packet (the last couple of thousand)
_INDEXES: dict = {}


def _remember(d: dict, key, value, keep: int):
    d[key] = d.pop(key, value)
    while len(d) > keep:
        d.pop(next(iter(d)))
    return value


def world_at(recordings: list[dict], chunks_wanted: set[tuple[int, int]], dim: str, cache_dir: str | None
             ) -> dict[tuple[int, int], RecChunk]:
    """The chunks (of *chunks_wanted*, in *dim*) as the recordings saw them. *recordings*: ``{"zip", "tick",
    "when": tick -> world time ms}`` for every recording running at that moment."""
    best: dict[tuple[int, int], tuple] = {}             # (cx, cz) -> (load ms, recording, entry, proto)
    all_edits: list[tuple] = []                         # (ms, proto, x, y, z, state id)
    states_of: dict[int, tuple[list[str], list[str]]] = {}
    for rec in recordings:
        sk = (rec["zip"], rec["tick"])
        st = _STATES.get(sk) or _remember(_STATES, sk, recording_state(*sk), 12)
        if not st:
            continue
        proto = st["proto"]
        if proto not in states_of:
            states_of[proto] = (protocol.block_states(protocol.table(proto, st["version"])), st["biomes"])
        idx = _INDEXES.get(rec["zip"]) or _remember(_INDEXES, rec["zip"], CacheIndex(rec["zip"], cache_dir), 32)
        try:
            with zipfile.ZipFile(rec["zip"]) as z:          # only the packets of the chunks wanted
                mine = [ld for ld in st["loads"] if ld[0] == dim and idx.where(z, ld[2]) in chunks_wanted]
                need = [ld[2] for ld in mine if (rec["zip"], ld[2]) not in _PACKETS]
                if need:
                    for e, pkt in idx.read(z, need).items():
                        _remember(_PACKETS, (rec["zip"], e), pkt, 3000)
                idx.save()
        except (OSError, zipfile.BadZipFile):
            continue
        packets = {ld[2]: _PACKETS.get((rec["zip"], ld[2])) for ld in mine}
        for d, lt, entry in mine:
            p = packets.get(entry)
            if not p:
                continue
            cxz = chunk_xz(p)
            if cxz not in chunks_wanted:
                continue
            ms = rec["when"](lt)
            if cxz not in best or best[cxz][0] <= ms:
                best[cxz] = (ms, p, proto, st["biomes"])
        for t, d, x, y, zz, sid in st["edits"]:
            if d == dim and (x >> 4, zz >> 4) in chunks_wanted:
                all_edits.append((rec["when"](t), proto, x, y, zz, sid))
    out: dict[tuple[int, int], RecChunk] = {}
    for cxz, (ms, p, proto, biomes) in best.items():
        try:
            c = RecChunk(p, proto, states_of[proto][0], biomes, dim)
        except (IndexError, ValueError, struct.error):
            continue
        out[cxz] = c
        c.loaded_ms, c.proto = ms, proto
    for ms, proto, x, y, zz, sid in sorted(all_edits):
        c = out.get((x >> 4, zz >> 4))
        if c is not None and ms >= c.loaded_ms and proto == c.proto:
            c.apply(x & 15, y, zz & 15, sid)
            c.last_ms = ms
    return out


def edits_between(zip_path: str, tick0: int, tick1: int) -> list[list]:
    """Every block change a recording saw from *tick0* to *tick1*: ``[[tick, dim, x, y, z, state id], ...]``."""
    try:
        with zipfile.ZipFile(zip_path) as z:
            meta = json.loads(z.read("metadata.json"))
            names = paths_mod._chunk_names(z)
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return []
    out, start = [], 0
    for n in names:
        end = start + int(((meta.get("chunks") or {}).get(n) or {}).get("duration") or 6000)
        if end > tick0 and start <= tick1:
            key = (zip_path, min(end - 1, tick1))
            st = _STATES.get(key) or _remember(_STATES, key, recording_state(*key), 12)
            if st:
                out += [e for e in st["edits"] if tick0 <= e[0] <= tick1]
        start = end
    return out
