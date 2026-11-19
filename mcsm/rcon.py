"""Minimal RCON client (the "Source RCON" protocol Minecraft servers speak).

Enable on the server in ``server.properties``::

    enable-rcon=true
    rcon.port=25575
    rcon.password=<a long random password>

Packet layout (all integers little-endian)::

    int32 length      (of everything after this field)
    int32 request_id  (echoed back in the response; -1 means auth failed)
    int32 type        (3 = login, 2 = command, 0 = response)
    bytes body        (UTF-8)
    b"\\x00\\x00"     (body terminator + empty string)

**Security:** RCON is *not encrypted* and gives full operator access. Use a long
random password and only connect over networks you trust. This client only
ever sends read-only commands (``list`` and ``data get ...``) -- see
:mod:`mcsm.live`.
"""

from __future__ import annotations

import socket
import struct
import threading

TYPE_LOGIN = 3
TYPE_COMMAND = 2
TYPE_RESPONSE = 0
MAX_BODY = 1446  # vanilla rejects command packets larger than this


class RconError(Exception):
    """Connection, protocol or authentication problem."""


class RconClient:
    def __init__(self, host: str, port: int = 25575, password: str = "", timeout: float = 5.0):
        self.host, self.port, self.password, self.timeout = host, int(port), password, timeout
        self.sock: socket.socket | None = None
        self._next_id = 1
        self._lock = threading.Lock()

    # -- connection ---------------------------------------------------------
    def connect(self) -> None:
        self.close()
        try:
            self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        except OSError as exc:
            raise RconError(f"cannot connect to {self.host}:{self.port} ({exc}). Is enable-rcon=true "
                            f"and is the RCON port open on your host?") from exc
        self.sock.settimeout(self.timeout)
        rid = self._send(TYPE_LOGIN, self.password)
        # Some servers send an empty RESPONSE before the auth reply; skip until we see our id or -1.
        for _ in range(3):
            got_id, _type, _body = self._read_packet()
            if got_id == -1:
                self.close()
                raise RconError("RCON password rejected")
            if got_id == rid:
                return
        self.close()
        raise RconError("unexpected RCON login reply")

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()

    # -- commands -----------------------------------------------------------
    def command(self, cmd: str) -> str:
        return self.commands([cmd])[0]

    def commands(self, cmds: list[str]) -> list[str]:
        """Send several commands at once (pipelined) and return their replies in order.

        Pipelining matters over the internet: 30 commands cost one round trip
        instead of thirty.
        """
        with self._lock:
            if self.sock is None:
                self.connect()
            try:
                ids = [self._send(TYPE_COMMAND, c) for c in cmds]
                replies: dict[int, list[str]] = {i: [] for i in ids}
                pending = set(ids)
                while pending:
                    rid, _type, body = self._read_packet()
                    if rid in replies:
                        replies[rid].append(body)
                        # Vanilla splits replies over 4096 bytes into several packets
                        # with the same id; replies we use are small, so the first
                        # packet ends the reply unless it is exactly full.
                        if len(body.encode("utf-8")) < 4096:
                            pending.discard(rid)
                return ["".join(replies[i]) for i in ids]
            except (OSError, RconError):
                self.close()
                raise

    # -- wire format --------------------------------------------------------
    def _send(self, ptype: int, body: str) -> int:
        data = body.encode("utf-8")
        if len(data) > MAX_BODY:
            raise RconError("command too long for RCON")
        rid = self._next_id
        self._next_id = self._next_id + 1 if self._next_id < 2**31 - 2 else 1
        payload = struct.pack("<ii", rid, ptype) + data + b"\x00\x00"
        assert self.sock is not None
        self.sock.sendall(struct.pack("<i", len(payload)) + payload)
        return rid

    def _recv_exact(self, n: int) -> bytes:
        assert self.sock is not None
        buf = bytearray()
        while len(buf) < n:
            try:
                chunk = self.sock.recv(n - len(buf))
            except socket.timeout as exc:
                raise RconError("RCON server did not answer in time") from exc
            if not chunk:
                raise RconError("RCON connection closed by server")
            buf += chunk
        return bytes(buf)

    def _read_packet(self) -> tuple[int, int, str]:
        (length,) = struct.unpack("<i", self._recv_exact(4))
        if not 10 <= length <= 1 << 20:
            raise RconError(f"bad RCON packet length {length}")
        data = self._recv_exact(length)
        rid, ptype = struct.unpack("<ii", data[:8])
        return rid, ptype, data[8:-2].decode("utf-8", errors="replace")
