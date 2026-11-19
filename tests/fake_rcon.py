"""A fake Minecraft RCON server for tests and manual end-to-end checks.

Speaks the real Source RCON protocol and answers the commands the live map
uses with vanilla-formatted replies. Run it by hand to try ``mcsm live``
without a real server::

    python tests/fake_rcon.py            # listens on 127.0.0.1:25575, password "test"
    mcsm live --host 127.0.0.1 --rcon-port 25575     (password: test)
"""

from __future__ import annotations

import math
import re
import socket
import struct
import sys
import threading
import time


class FakeRconServer:
    def __init__(self, password: str = "test", host: str = "127.0.0.1", port: int = 0):
        self.password = password
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((host, port))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self.commands: list[str] = []
        self.t0 = time.time()
        # name -> dict; edit from tests to simulate movement/joins/leaves
        self.players = {
            "Steve": {"pos": [12.5, 64.0, -3.25], "rot": [90.0, 10.0], "dim": "minecraft:overworld",
                      "health": 18.5, "food": 17, "xp": 30, "mode": 0},
            "Alex": {"pos": [-100.0, 70.0, 250.75], "rot": [-45.5, 0.0], "dim": "minecraft:the_nether",
                     "health": 20.0, "food": 20, "xp": 3, "mode": 1},
        }
        self.vanish_after_list: set[str] = set()   # names that disappear between "list" and "data get"
        self._stop = False
        threading.Thread(target=self._accept, daemon=True).start()

    def close(self):
        self._stop = True
        try:
            self.sock.close()
        except OSError:
            pass

    # -- protocol -------------------------------------------------------------
    def _accept(self):
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    @staticmethod
    def _send(conn, rid, ptype, body):
        data = struct.pack("<ii", rid, ptype) + body.encode() + b"\x00\x00"
        conn.sendall(struct.pack("<i", len(data)) + data)

    def _serve(self, conn):
        authed = False
        try:
            while True:
                head = conn.recv(4, socket.MSG_WAITALL)
                if len(head) < 4:
                    return
                (n,) = struct.unpack("<i", head)
                data = conn.recv(n, socket.MSG_WAITALL)
                rid, ptype = struct.unpack("<ii", data[:8])
                body = data[8:-2].decode()
                if ptype == 3:
                    authed = body == self.password
                    self._send(conn, rid if authed else -1, 2, "")
                    continue
                if not authed:
                    self._send(conn, -1, 2, "")
                    continue
                self.commands.append(body)
                self._send(conn, rid, 0, self.reply(body))
        except OSError:
            return
        finally:
            conn.close()

    # -- command replies (vanilla wording) --------------------------------------
    def reply(self, cmd: str) -> str:
        if cmd == "list":
            names = list(self.players)
            for n in self.vanish_after_list:
                self.players.pop(n, None)
            return f"There are {len(names)} of a max of 20 players online: {', '.join(names)}"
        m = re.fullmatch(r"data get entity (\S+) (\S+)", cmd)
        if not m:
            return f"Unknown or incomplete command, see below for error{cmd}<--[HERE]"
        name, path = m.groups()
        p = self.players.get(name)
        if p is None:
            return "No entity was found"
        value = {
            "Pos": lambda: "[" + ", ".join(f"{v}d" for v in p["pos"]) + "]",
            "Rotation": lambda: "[" + ", ".join(f"{v}f" for v in p["rot"]) + "]",
            "Dimension": lambda: f'"{p["dim"]}"',
            "Health": lambda: f'{p["health"]}f',
            "foodLevel": lambda: str(p["food"]),
            "XpLevel": lambda: str(p["xp"]),
            "playerGameType": lambda: str(p["mode"]),
        }.get(path)
        if value is None:
            return f"Found no elements matching {path}"
        return f"{name} has the following entity data: {value()}"

    def animate(self):
        """Move players in circles (for manual runs)."""
        while not self._stop:
            t = time.time() - self.t0
            for i, p in enumerate(self.players.values()):
                p["pos"][0] = 40 * math.cos(t / 10 + i)
                p["pos"][2] = 40 * math.sin(t / 10 + i)
                p["rot"][0] = (math.degrees(t / 10 + i) + 180) % 360 - 180
            time.sleep(0.2)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 25575
    srv = FakeRconServer(port=port)
    for p in srv.players.values():
        p["dim"] = "minecraft:overworld"
    print(f"Fake RCON server on 127.0.0.1:{srv.port}, password 'test'. Ctrl+C to stop.")
    try:
        srv.animate()
    except KeyboardInterrupt:
        srv.close()
