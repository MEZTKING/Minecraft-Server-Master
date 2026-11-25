"""Searching everything that happened, by text (the history viewer's dev mode: "Search what happened").

Three sources, each turned into one line of plain text per thing that happened:

- the server logs: joins, leaves, chat, commands, deaths, advancements, starts and crashes;
- the recordings (each one read in full, the exact-playback data): what was on the recorded player's screen
  (messages, the action bar, titles, boss bars), deaths and hurts, blocks broken, items picked up, dropped or held,
  armour, signs written, chests opened, mobs (their names, babies, variants, anger, tamed, deaths, hearts...),
  potion effects, particles, level events, game modes, sizes, totems, leads, paintings and frames...;
- the Ledger block log: every block broken, placed or changed, items put in or taken out of containers, picked up or
  dropped, entities killed or changed, with who or what did it.

A search matches lines holding every word of the query (any case). Lines that only repeat the one before (the same
player or mob, the same kind, the same text within 10 s) are left out.
"""
from __future__ import annotations

import gzip
import json
import os
import re

# kinds whose value starts with x, y, z (a line for them takes the camera to the place)
POSITIONED = {"bb", "sg", "ce", "pi", "le", "pt", "mc"}
# event kinds not worth a line: they change many times a second (flags, swings, the held slot, health, hand use,
# boats' paddles, arrows in the ground, mining stages) or say nothing on their own (left view)
NOISY = {"f", "s", "hs", "hp", "h", "pl", "pr", "ig", "asp", "gone", "mc", "ar", "air", "fz"}

ENTITY_EVENTS = {4: "attacking", 8: "shaking itself dry", 11: "holding out a poppy", 34: "putting the poppy away",
                 6: "taming failed (smoke)", 7: "tamed (hearts)", 10: "eating grass", 12: "villager hearts", 13: "villager angry",
                 14: "villager happy", 18: "in love (hearts)", 20: "appeared from a spawner (poof)",
                 60: "body vanished after dying (poof)"}
POSES = {0: "standing", 1: "flying with elytra", 2: "sleeping", 3: "swimming", 4: "spin attack", 5: "sneaking",
         6: "long jumping", 7: "dying", 8: "croaking", 9: "using tongue", 10: "sitting", 11: "roaring", 12: "sniffing",
         13: "emerging", 14: "digging", 15: "sliding", 16: "shooting", 17: "inhaling"}
GAME_MODES = {0: "survival", 1: "creative", 2: "adventure", 3: "spectator"}
LABELS = {"d": "died", "hurt": "was hurt", "totem": "used a totem of undying", "ab": "action bar:", "sm": "message:",
          "ti": "title:", "st": "subtitle:", "ta": "title times", "tc": "titles cleared", "bo": "boss bars:",
          "ang": "angry for ticks", "ef": "potion effect swirls", "gm": "game mode", "pk": "picked up by",
          "bb": "block broken", "pi": "piston", "le": "level event", "pt": "particles", "sg": "sign written",
          "lead": "lead", "hg": "painting or frame hung", "hv": "painting", "hi": "item frame holds", "hgx": "frame gone",
          "fb": "falling block", "it": "dropped item", "ce": "chest opened or closed", "sc": "size", "p": "pose",
          "baby": "baby", "variant": "variant", "name": "named", "vd": "villager", "tame": "tamed flags",
          "collar": "collar colour", "wool": "wool colour", "size": "slime size", "weather": "copper age",
          "asf": "armour stand", "r": "riding", "it_": "item", "ee": "", "fx": "level event"}


def component_plain(c, lang: dict | None = None, depth: int = 0) -> str:
    """A text component's plain text, translation keys put into English with *lang* (the game's en_us.json)."""
    if c is None or depth > 20:
        return ""
    if isinstance(c, (str, int, float)):
        return re.sub("§.", "", str(c))
    if isinstance(c, list):
        return "".join(component_plain(x, lang, depth + 1) for x in c)
    if not isinstance(c, dict):
        return ""
    out = ""
    if "translate" in c:
        fmt = (lang or {}).get(c["translate"]) or c.get("fallback") or c["translate"]
        args = [component_plain(a, lang, depth + 1) for a in c.get("with") or []]
        i = 0

        def arg(m):
            nonlocal i
            if m.group(2) == "%":
                return "%"
            k = int(m.group(1)) - 1 if m.group(1) else i
            if not m.group(1):
                i += 1
            return args[k] if 0 <= k < len(args) else ""
        out = re.sub(r"%(?:(\d+)\$)?([s%])", arg, fmt)
    elif "keybind" in c:
        out = str(c["keybind"])
    elif "score" in c:
        out = str((c.get("score") or {}).get("value", ""))
    elif "selector" in c:
        out = str(c["selector"])
    else:
        out = str(c.get("text", ""))
    out = re.sub("§.", "", out)
    return out + "".join(component_plain(x, lang, depth + 1) for x in c.get("extra") or [])


def who_text(name: str | None) -> str:
    """'#wolf#123' -> 'wolf #123'; players as they are."""
    if name and name.startswith("#"):
        parts = name.split("#")
        return f"{parts[1].replace('_', ' ')} #{parts[2]}" if len(parts) > 2 else parts[1]
    return name or ""


def event_text(kind: str, value, lang: dict | None = None) -> str | None:
    """One recorded event (exact-playback data) as text; None: not worth a line."""
    if kind in NOISY:
        return None
    if kind.startswith(("eq:", "hb:")):
        slot = kind.split(":", 1)[1]
        item = value[0] if isinstance(value, list) and value else None
        if kind.startswith("hb:"):
            return f"hotbar slot {int(slot) + 1}: {item or 'empty'}" if slot.isdigit() else None
        verb = "holds" if slot in ("mainhand", "offhand") else "wears"
        return f"{verb} ({slot}) {item or 'nothing'}"
    if kind in ("ab", "sm", "ti", "st"):
        return f"{LABELS[kind]} {component_plain(value, lang)}"
    if kind == "bo":
        bars = value or []
        return "boss bars: " + (", ".join(f"{component_plain(b.get('title'), lang)} ({round(100 * (b.get('health') or 0))}%)"
                                         for b in bars) or "none")
    if kind == "xp" and isinstance(value, list) and len(value) > 2:
        return f"experience level {value[1]} ({value[2]} points)"
    if kind == "eff" and isinstance(value, list):
        return "effects: " + (", ".join(f"{e[0].replace('_', ' ')} {e[1] + 1}" for e in value) or "none")
    if kind == "cs":
        return "swelling (about to explode)" if value == 1 else "calming down"
    if kind == "cp":
        return "charged" if value else "not charged"
    if kind == "eb":
        return f"carrying {value}" if value else "carrying nothing"
    if kind == "ec":
        return "screaming (stared at)" if value else "calm"
    if kind == "gc":
        return "about to shoot" if value else "not shooting"
    if kind in ("wi", "hf", "sha", "spk", "sco"):
        return None
    if kind == "ee":
        return ENTITY_EVENTS.get(value, f"entity event {value}")
    if kind == "ang":
        return f"angry ({value} ticks)" if value else "calm (not angry)"
    if kind == "p":
        return f"pose: {POSES.get(value, value)}"
    if kind == "gm":
        return f"game mode: {GAME_MODES.get(value, value)}"
    if kind == "d":
        return "died"
    if kind == "hurt":
        return "was hurt"
    if kind == "totem":
        return "used a totem of undying"
    if kind == "pk":
        return f"picked up by {value}" if value else "picked up"
    if kind == "ef":
        lst = value[0] if isinstance(value, list) and value else []
        if not lst:
            return "potion effects ended"
        cols = ", ".join(f"#{(c or 0) & 0xFFFFFF:06x}" if n == "entity_effect" else n for n, c in lst)
        return f"potion effect swirls{' (beacon)' if value[1] else ''}: {cols}"
    if kind == "bb" and isinstance(value, list):        # [x, y, z, block]: the place goes with the line
        return "block broken: " + " ".join(str(v) for v in value[3:])
    if kind == "sg" and isinstance(value, list):
        lines = [component_plain(x, lang) for part in value[3:5] if isinstance(part, list) for x in part]
        return "sign written: " + " / ".join(x for x in lines if x)
    if kind == "pt" and isinstance(value, list) and len(value) > 8:
        return f"particles: {value[8]}" + (f" {value[9]}" if isinstance(value[9], str) else "")
    label = LABELS.get(kind, kind)
    if isinstance(value, (dict, list)):
        v = json.dumps(value, separators=(",", ":"))
        return f"{label} {v[:160]}"
    return f"{label} {value}" if value not in (None, True, 1) else label


def index_events(events: list, when, aliases: dict, recorded: str, lang: dict | None) -> list[list]:
    """``[[ms, who, kind, text (, x, y, z)], ...]`` for a recording's events (each repeat within 10 s left out)."""
    out, last = [], {}
    for tick, w, kind, value in events:
        if kind in ("eq", "hb"):
            kind, value = f"{kind}:{value[0]}", value[1:]
        try:
            text = event_text(kind, value, lang)
        except (TypeError, ValueError, KeyError, IndexError):
            text = None
        if not text:
            continue
        name = w if w and w.startswith("#") else aliases.get(w or recorded, w or recorded)
        t = int(when(tick))
        key = (name, kind)
        if key in last and last[key][1] == text and t - last[key][0] < 10_000:
            continue
        last[key] = (t, text)
        row = [t, name, kind, text]
        if kind in POSITIONED and isinstance(value, list) and len(value) >= 3                 and all(isinstance(v, (int, float)) for v in value[:3]):
            row += [round(value[0], 1), round(value[1], 1), round(value[2], 1)]
        out.append(row)
    return out


def exact_version(suffix: str) -> int:
    """The number in an exact-playback data suffix (``.exact29.json.gz`` -> 29)."""
    m = re.search(r"\.exact(\d+)\.json\.gz$", suffix)
    return int(m.group(1)) if m else 0


def index_suffix(version: int) -> str:
    """A recording's searchable lines made from its version-*version* exact data."""
    return f".search3-x{version}.json.gz"


def exact_of(cache: str, key: str, version: int) -> dict | None:
    """A recording's exact-playback data of one version (``<key>.exactNN.json.gz``), or None."""
    path = os.path.join(cache, f"{key}.exact{version}.json.gz")
    if not os.path.isfile(path):
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError, EOFError):
        return None


def load_index(path: str) -> list | None:
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError, EOFError):
        return None


def save_index(path: str, rows: list) -> None:
    with gzip.open(path + ".part", "wt", encoding="utf-8") as fh:
        json.dump(rows, fh, separators=(",", ":"))
    os.replace(path + ".part", path)


def terms_of(q: str) -> list[str]:
    return [t for t in q.lower().split() if t]


def matches(text: str, terms: list[str]) -> bool:
    low = text.lower()
    return all(t in low for t in terms)


def ledger_search(lg, terms: list[str], limit: int = 200) -> list[dict]:
    """Ledger rows whose action, block or item (new or old), source or player hold every term (newest first):
    ``{t, who, kind: "ledger", text, x, y, z, dim}``."""
    if lg is None or not terms:
        return []
    from . import ledger as ledger_mod
    con = lg._con()
    players = dict(con.execute("select id, player_name from players"))
    sources = dict(con.execute("select id, name from sources"))
    actions = {i: n for n, i in lg.actions.items()}
    worlds = {i: n for n, i in lg.worlds.items()}
    objs = lg.objects
    conds, args = [], []
    for t in terms:
        ids = {
            "action_id": [i for i, n in actions.items() if t in n.lower()],
            "object_id": [i for i, n in objs.items() if t in n.lower()],
            "old_object_id": [i for i, n in objs.items() if t in n.lower()],
            "source": [i for i, n in sources.items() if t in n.lower()],
            "player_id": [i for i, n in players.items() if t in n.lower()],
        }
        part = [f"{col} in ({','.join('?' * len(v))})" for col, v in ids.items() if v]
        if not part:
            return []
        conds.append("(" + " or ".join(part) + ")")
        for v in ids.values():
            args.extend(v)
    q = ("select time, x, y, z, world_id, action_id, object_id, old_object_id, source, player_id from actions where "
         + " and ".join(conds) + " order by id desc limit ?")
    out = []
    for when, x, y, z, w, a, o, oo, src, p in con.execute(q, args + [limit]):
        act = actions.get(a, "?")
        who = players.get(p) or sources.get(src, "?")
        obj = objs.get(o, "?").replace("minecraft:", "")
        old = objs.get(oo, "").replace("minecraft:", "")
        text = f"{act} {obj}" + (f" (was {old})" if old and old != obj and old != "air" else "") + \
               (f" by {sources.get(src)}" if not players.get(p) and sources.get(src) else "")
        out.append({"t": ledger_mod._ms(when), "who": who, "kind": "ledger", "text": text, "x": x, "y": y, "z": z,
                    "dim": worlds.get(w, "")})
    return out
