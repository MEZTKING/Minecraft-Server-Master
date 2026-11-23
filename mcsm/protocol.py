"""Per-version network details of Minecraft, for reading recordings: packet ids and entity types.

Game packets inside a Flashback recording are numbered per protocol version. The numbers (and the list of entity
types, by their network id) come from the game's own data reports: ``net.minecraft.data.Main --reports`` writes
``packets.json`` and ``registries.json``. The tables for the versions the app has met are bundled
(``assets/protocols.json``); for any other version, :func:`table` runs the report tool from the Minecraft client
jar already on this PC (with the libraries the launcher keeps next to it and a Java it has installed) and keeps
the result in ``%LOCALAPPDATA%/mcsm/protocol/``. Nothing is downloaded.
"""

from __future__ import annotations

import glob
import json
import os
import subprocess
import tempfile
import zipfile

PACKETS = ("add_entity", "remove_entities", "set_entity_data", "animate", "set_time", "entity_event",
           "set_equipment", "set_passengers", "hurt_animation", "damage_event", "game_event",
           "level_chunk_with_light", "block_update", "section_blocks_update", "forget_level_chunk", "respawn")
TABLE_VERSION = 3          # 3: block states and the chunk packets (blocks at the moment); 2: items and components
BUNDLED = os.path.join(os.path.dirname(__file__), "assets", "protocols.json")
_cache: dict[int, dict | None] = {}


def cache_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "mcsm", "protocol")


def from_reports(folder: str, version: str, protocol: int) -> dict:
    """The table from a reports folder (``reports/packets.json``, ``reports/registries.json``)."""
    with open(os.path.join(folder, "reports", "packets.json"), encoding="utf-8") as fh:
        packets = json.load(fh)["play"]["clientbound"]
    with open(os.path.join(folder, "reports", "registries.json"), encoding="utf-8") as fh:
        entries = json.load(fh)["minecraft:entity_type"]["entries"]
    types = [""] * (max(v["protocol_id"] for v in entries.values()) + 1)
    for name, v in entries.items():
        types[v["protocol_id"]] = name.split(":", 1)[-1]
    with open(os.path.join(folder, "reports", "registries.json"), encoding="utf-8") as fh:
        regs = json.load(fh)

    def listed(reg: str) -> list[str]:          # a registry's names by network id
        e = (regs.get(reg) or {}).get("entries") or {}
        out = [""] * (max((v["protocol_id"] for v in e.values()), default=-1) + 1)
        for name, v in e.items():
            out[v["protocol_id"]] = name.split(":", 1)[-1]
        return out
    return {"version": version, "protocol": protocol,
            "packets": {k: packets[f"minecraft:{k}"]["protocol_id"] for k in PACKETS if f"minecraft:{k}" in packets},
            "entity_types": types, "items": listed("minecraft:item"),
            "components": listed("minecraft:data_component_type"),
            "blocks": _block_table(os.path.join(folder, "reports", "blocks.json"))}


def _block_table(path: str) -> list:
    """Every block state by network id, compactly: ``[[name, [[property, [values]], ...]], ...]`` in id order.
    The game numbers a block's states as every combination of its properties, sorted by property name, the last
    changing fastest (checked here against every id in the report)."""
    import itertools
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as fh:
        blocks = json.load(fh)
    ids = {s["id"]: (name, tuple(sorted((s.get("properties") or {}).items())))
           for name, v in blocks.items() for s in v["states"]}
    out, n = [], 0
    for name, v in sorted(blocks.items(), key=lambda kv: min(s["id"] for s in kv[1]["states"])):
        props = sorted((v.get("properties") or {}).items())
        for combo in itertools.product(*[vals for _, vals in props]):
            if ids.get(n) != (name, tuple(zip([p for p, _ in props], combo))):
                raise ValueError(f"block state {n} isn't where expected ({name})")
            n += 1
        out.append([name.split(":", 1)[-1], [[p, vals] for p, vals in props]])
    return out


def block_states(t: dict | None) -> list[str]:
    """``minecraft:oak_stairs[facing=east,half=top,...]`` for every block state id of a protocol table."""
    import itertools
    out = []
    for name, props in (t or {}).get("blocks") or []:
        for combo in itertools.product(*[vals for _, vals in props]):
            out.append(f"minecraft:{name}" + ("[" + ",".join(f"{p}={v}" for (p, _), v in zip(props, combo)) + "]"
                                              if props else ""))
    return out


def _launcher_classpath(version: str) -> tuple[list[str], str | None]:
    """The client jar and its libraries from Prism Launcher, and a Java that can run that version."""
    from .blockmesh import find_client_jar
    jar = find_client_jar(version)
    prism = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "PrismLauncher")
    cp = [jar] if jar and version in os.path.basename(jar) else []
    try:
        with open(os.path.join(prism, "meta", "net.minecraft", version + ".json"), encoding="utf-8") as fh:
            meta = json.load(fh)
    except (OSError, ValueError):
        return cp, None
    for lib in meta.get("libraries", []):
        parts = (lib.get("name") or "").split(":")
        if len(parts) < 3 or "natives" in lib.get("name", "") or lib.get("rules"):
            continue
        g, a, v = parts[:3]
        p = os.path.join(prism, "libraries", *g.split("."), a, v, f"{a}-{v}.jar")
        if os.path.isfile(p):
            cp.append(p)
    major = (meta.get("javaVersion") or {}).get("majorVersion")
    first = version.split(".")[0]
    major = major or (25 if first.isdigit() and int(first) >= 26 else 21)
    javas = glob.glob(os.path.join(prism, "java", "*", "bin", "java.exe")) + glob.glob(
        os.path.join(prism, "java", "*", "bin", "java"))
    javas += glob.glob(r"C:\Program Files\Eclipse Adoptium\*\bin\java.exe")
    java = next((j for j in javas if f"jre{major}" in j or f"jdk-{major}" in j or f"jre-{major}" in j), None)
    return cp, java


def generate(version: str) -> dict | None:
    """Run the game's report tool for *version* (from this PC's client jar) and keep the table."""
    cp, java = _launcher_classpath(version)
    if not cp or not java:
        return None
    try:
        protocol = json.loads(zipfile.ZipFile(cp[0]).read("version.json")).get("protocol_version")
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return None
    with tempfile.TemporaryDirectory() as tmp:
        try:
            subprocess.run([java, "-cp", os.pathsep.join(cp), "net.minecraft.data.Main", "--reports", "--output",
                            os.path.join(tmp, "out")], cwd=tmp, capture_output=True, timeout=600, check=True)
            t = from_reports(os.path.join(tmp, "out"), version, int(protocol))
        except (OSError, subprocess.SubprocessError, ValueError, KeyError):
            return None
    os.makedirs(cache_dir(), exist_ok=True)
    with open(os.path.join(cache_dir(), f"{t['protocol']}.json"), "w", encoding="utf-8") as fh:
        json.dump(t, fh)
    return t


def table(protocol: int | None, version: str | None = None) -> dict | None:
    """The table for *protocol* (bundled, kept, or generated from *version*'s client jar), or None."""
    if protocol is None:
        return None
    if protocol in _cache:
        return _cache[protocol]
    t = None
    try:
        with open(BUNDLED, encoding="utf-8") as fh:
            t = json.load(fh).get(str(protocol))
    except (OSError, ValueError):
        pass
    if t is None:
        try:
            with open(os.path.join(cache_dir(), f"{protocol}.json"), encoding="utf-8") as fh:
                t = json.load(fh)
        except (OSError, ValueError):
            t = None
    if t is not None and ("items" not in t or "blocks" not in t) and version:   # made by an older app: again
        t = None
    if t is None and version:
        t = generate(version)
        if t is not None and t.get("protocol") != protocol:
            t = None
    _cache[protocol] = t
    return t
