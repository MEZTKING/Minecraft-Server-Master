"""Time-zone handling.

The core difficulty of this project is that different files store time
differently:

* **Log files** store only a wall-clock time (``[20:15:03]``) in the
  *server's local time zone*. The date comes from the file name.
* **Advancement files** store ``2024-05-01 20:15:03 +0200`` -- local time
  *with* the UTC offset. This tells us the server's time zone for free.
* **Replay files** (``.mcpr``) store a Unix timestamp in milliseconds (UTC).

Everything in this project is converted to timezone-aware UTC
``datetime`` objects as early as possible. :class:`LogClock` is the piece
that turns a naive local log time into UTC.
"""

from __future__ import annotations

import bisect
import re
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Iterable

UTC = timezone.utc

_OFFSET_RE = re.compile(r"^(?:UTC|GMT)?\s*([+-])(\d{1,2}):?(\d{2})?$", re.IGNORECASE)


def parse_tz(spec: str) -> tzinfo:
    """Parse a time-zone spec.

    Accepts an IANA name (``Europe/Amsterdam``), ``UTC``, or a fixed
    offset (``+02:00``, ``-0500``, ``UTC+1``).

    IANA names need the system time-zone database. On Windows install it
    with ``pip install tzdata``.
    """
    spec = spec.strip()
    if spec.upper() in ("UTC", "Z", "GMT"):
        return UTC
    m = _OFFSET_RE.match(spec)
    if m:
        sign, hours, minutes = m.group(1), int(m.group(2)), int(m.group(3) or 0)
        delta = timedelta(hours=hours, minutes=minutes)
        return timezone(-delta if sign == "-" else delta)
    try:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    except ImportError as exc:  # pragma: no cover - Python < 3.9
        raise ValueError("IANA time zones need Python 3.9+") from exc
    try:
        return ZoneInfo(spec)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(
            f"unknown time zone {spec!r} (on Windows run: pip install tzdata; "
            f"or use a fixed offset such as +02:00)"
        ) from exc


def parse_offset_timestamp(text: str) -> datetime | None:
    """Parse advancement timestamps like ``2024-05-01 20:15:03 +0200``."""
    try:
        return datetime.strptime(text.strip(), "%Y-%m-%d %H:%M:%S %z")
    except ValueError:
        return None


def from_epoch_ms(ms: int | float) -> datetime:
    return datetime.fromtimestamp(ms / 1000.0, tz=UTC)


def to_epoch_ms(dt: datetime) -> int:
    return int(round(dt.timestamp() * 1000))


def iso(dt: datetime | None) -> str | None:
    """UTC ISO-8601 string with a trailing ``Z``, or ``None``."""
    if dt is None:
        return None
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class LogClock:
    """Converts naive local times found in logs into UTC.

    Resolution order:

    1. An explicit time zone given by the user (``--tz``) -- always wins.
    2. Offsets learned from advancement timestamps. Each sample is a
       ``(local date, utc offset)`` pair; for a given log date the offset
       of the nearest sampled date is used, so daylight-saving changes are
       handled as long as there are advancements on both sides of them.
    3. UTC, with :attr:`source` set to ``"assumed-utc"`` so the UI can
       warn about it.
    """

    def __init__(self, explicit: tzinfo | None = None,
                 samples: Iterable[tuple[date, timedelta]] = ()):
        self.explicit = explicit
        by_date: dict[date, dict[timedelta, int]] = {}
        for d, off in samples:
            by_date.setdefault(d, {}).setdefault(off, 0)
            by_date[d][off] += 1
        # One offset per date: the most common one that day.
        self._dates = sorted(by_date)
        self._offsets = [max(by_date[d].items(), key=lambda kv: kv[1])[0] for d in self._dates]
        if explicit is not None:
            self.source = "explicit"
        elif self._dates:
            self.source = "advancements"
        else:
            self.source = "assumed-utc"

    def offset_for(self, local: datetime) -> timedelta:
        if self.explicit is not None:
            return local.replace(tzinfo=self.explicit).utcoffset() or timedelta(0)
        if not self._dates:
            return timedelta(0)
        d = local.date()
        i = bisect.bisect_left(self._dates, d)
        if i == 0:
            return self._offsets[0]
        if i >= len(self._dates):
            return self._offsets[-1]
        before, after = self._dates[i - 1], self._dates[i]
        if after == d:
            return self._offsets[i]
        return self._offsets[i - 1] if (d - before) <= (after - d) else self._offsets[i]

    def to_utc(self, local: datetime) -> datetime:
        """Convert a naive local datetime to an aware UTC datetime."""
        if local.tzinfo is not None:
            return local.astimezone(UTC)
        return (local - self.offset_for(local)).replace(tzinfo=UTC)

    def describe(self) -> str:
        if self.source == "explicit":
            return f"explicit time zone {self.explicit}"
        if self.source == "advancements":
            offs = sorted({_fmt_offset(o) for o in self._offsets})
            return f"learned from advancement files (UTC offsets seen: {', '.join(offs)})"
        return "no time-zone information found; log times assumed to be UTC (use --tz)"


def _fmt_offset(off: timedelta) -> str:
    total = int(off.total_seconds())
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    return f"UTC{sign}{total // 3600:02d}:{(total % 3600) // 60:02d}"


def parse_duration(text: str) -> float:
    """Parse ``90``, ``1:30``, ``01:01:30``, ``1:30.5`` or ``1m30s`` into seconds."""
    text = text.strip()
    m = re.fullmatch(r"(?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:(\d+(?:\.\d+)?)s)?", text)
    if m and any(m.groups()):
        h, mi, s = m.groups()
        return int(h or 0) * 3600 + int(mi or 0) * 60 + float(s or 0)
    parts = text.split(":")
    if not 1 <= len(parts) <= 3:
        raise ValueError(f"cannot parse duration {text!r}")
    try:
        nums = [float(p) for p in parts]
    except ValueError as exc:
        raise ValueError(f"cannot parse duration {text!r}") from exc
    seconds = 0.0
    for n in nums:
        seconds = seconds * 60 + n
    return seconds


def format_hms(seconds: float) -> str:
    """``3725.4`` -> ``1:02:05``; under an hour -> ``2:05``."""
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
