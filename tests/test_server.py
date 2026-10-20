"""End-to-end tests: run host.py + server.chicken and talk to it over TCP."""
import os
import socket
import subprocess
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
import mcclient  # noqa: E402
from chasm import assemble, to_chicken  # noqa: E402


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # always test the program built from the current source
        path = os.path.join(ROOT, "server.chicken")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(to_chicken(assemble(os.path.join(ROOT, "src", "server.chasm"))))
        cls.port = free_port()
        cls.proc = subprocess.Popen(
            [sys.executable, os.path.join(ROOT, "host.py"), "--host", "127.0.0.1",
             "--port", str(cls.port), "--tick", "0.5"],
            stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", cls.port), 0.2).close()
                return
            except OSError:
                time.sleep(0.05)
        raise RuntimeError("host did not start")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait()

    def test_status_and_ping(self):
        info, pong = mcclient.status(port=self.port)
        self.assertEqual(info["version"], {"name": "1.8", "protocol": 47})
        self.assertEqual(info["players"]["max"], 20)
        self.assertIn("Chicken", info["description"]["text"])
        self.assertEqual(pong, 0x0123456789ABCDEF)

    def test_status_byte_by_byte(self):
        # framing is done in Chicken: feed the request one byte at a time
        c = mcclient.Conn(port=self.port)
        hs = mcclient.varint(47) + mcclient.string("localhost") + b"\x63\xdd" + mcclient.varint(1)
        raw = mcclient.varint(len(hs) + 1) + b"\x00" + hs + b"\x01\x00"
        for i in range(len(raw)):
            c.s.sendall(raw[i:i + 1])
            time.sleep(0.02)
        pid, r = c.recv()
        self.assertEqual(pid, 0)
        self.assertIn('"protocol":47', r.string())
        c.close()

    def test_offline_login(self):
        c, uuid, name = mcclient.login("Notch", port=self.port)
        self.assertEqual(name, "Notch")
        # derived from the name bytes: 'Notch' = 4e 6f 74 63 68
        self.assertEqual(uuid, "4e6f7463-6800-0000-0000-000000000000")
        c.close()

    def test_login_max_length_name(self):
        c, uuid, name = mcclient.login("ABCDEFGHIJKLMNOP", port=self.port)
        self.assertEqual(name, "ABCDEFGHIJKLMNOP")
        self.assertEqual(uuid, "41424344-4546-4748-494a-4b4c4d4e4f50")
        c.close()

    def test_join_game_and_world(self):
        import struct
        c, uuid, name = mcclient.login("Steve", port=self.port)
        pid, r = c.recv()
        self.assertEqual(pid, 0x01)                       # Join Game
        eid, gm, dim, diff, maxp = struct.unpack(">iBbBB", r.take(8))
        self.assertEqual((gm, dim), (1, 0))
        self.assertEqual(r.string(), "flat")
        pid, r = c.recv()
        self.assertEqual(pid, 0x05)                       # Spawn Position
        v = struct.unpack(">Q", r.take(8))[0]
        self.assertEqual((v >> 38, (v >> 26) & 0xFFF, v & 0x3FFFFFF), (8, 4, 8))
        coords = set()
        for _ in range(49):
            pid, r = c.recv()
            self.assertEqual(pid, 0x21)                   # Chunk Data
            x, z, full, mask = struct.unpack(">ii?H", r.take(11))
            coords.add((x, z))
            self.assertTrue(full)
            self.assertEqual(mask, 1)
            data = r.take(r.varint())
            self.assertEqual(r.rest(), b"")
            self.assertEqual(len(data), 4096 * 2 + 2048 + 2048 + 256)
            blocks = struct.unpack("<4096H", data[:8192])
            for y, want in ((0, 7), (1, 3), (2, 3), (3, 2), (4, 0), (15, 0)):
                self.assertEqual({blocks[y * 256 + i] >> 4 for i in range(256)}, {want})
            self.assertEqual(data[8192:10240], bytes(2048))
            self.assertEqual(data[10240:12288], bytes([255]) * 2048)
            self.assertEqual(data[12288:], bytes([1]) * 256)
        self.assertEqual(coords, {(x, z) for x in range(-3, 4) for z in range(-3, 4)})
        pid, r = c.recv()
        self.assertEqual(pid, 0x08)                       # Player Position And Look
        self.assertEqual(struct.unpack(">dddffb", r.take(33)), (8.5, 4.0, 8.5, 0.0, 0.0, 0))
        # the chicken avatar: invisible player, chicken spawned and riding it
        pid, r = c.recv()
        self.assertEqual((pid, r.rest()), (0x1C, bytes([1, 0x00, 0x20, 0x7F])))
        pid, r = c.recv()
        self.assertEqual(pid, 0x0F)
        self.assertEqual((r.varint(), r.take(1)[0]), (2, 93))
        self.assertEqual(struct.unpack(">iii", r.take(12)), (272, 128, 272))
        self.assertEqual(r.take(9), bytes(9))
        self.assertEqual(r.rest(), bytes([0x7F]))
        pid, r = c.recv()
        self.assertEqual((pid, struct.unpack(">ii?", r.take(9))), (0x1B, (2, 1, False)))
        c.close()

    def join(self, name):
        c, _, _ = mcclient.login(name, port=self.port)
        while c.recv()[0] != 0x1B:      # skip to the end of the join sequence
            pass
        return c

    def test_keep_alive_and_chat(self):
        import json
        c = self.join("Alex")
        # tick is 0.5 s in tests and a Keep Alive goes out every 5 ticks
        seen = []
        while len(seen) < 2:
            pid, r = c.recv()
            if pid == 0x00:
                kid = r.varint()
                seen.append(kid)
                c.send(0x00, mcclient.varint(kid))
        self.assertEqual(seen[1], seen[0] + 1)
        msg = 'hi "chicken" \\ \u00e9'
        c.send(0x01, mcclient.string(msg))
        while True:
            pid, r = c.recv()
            if pid == 0x02:
                break
        self.assertEqual(json.loads(r.string()), {"text": "<Alex> " + msg})
        self.assertEqual(r.take(1), b"\x00")
        c.close()


if __name__ == "__main__":
    unittest.main()
