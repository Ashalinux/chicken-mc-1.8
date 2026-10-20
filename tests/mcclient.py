"""Tiny Minecraft 1.8 test client (protocol 47), used only for testing."""
import json
import socket
import struct
import sys


def varint(n):
    out = b""
    n &= 0xFFFFFFFF
    while True:
        b = n & 0x7F
        n >>= 7
        out += bytes([b | (0x80 if n else 0)])
        if not n:
            return out


def string(s):
    b = s.encode("utf-8")
    return varint(len(b)) + b


class Reader:
    def __init__(self, data):
        self.d, self.i = data, 0

    def take(self, n):
        v = self.d[self.i:self.i + n]
        if len(v) < n:
            raise EOFError
        self.i += n
        return v

    def varint(self):
        n = shift = 0
        while True:
            b = self.take(1)[0]
            n |= (b & 0x7F) << shift
            shift += 7
            if not b & 0x80:
                return n

    def string(self):
        return self.take(self.varint()).decode("utf-8")

    def rest(self):
        return self.d[self.i:]


class Conn:
    def __init__(self, host="127.0.0.1", port=25565, timeout=10):
        self.s = socket.create_connection((host, port), timeout=timeout)
        self.buf = b""

    def send(self, pid, payload=b""):
        body = varint(pid) + payload
        self.s.sendall(varint(len(body)) + body)

    def recv(self):
        """Return (packet_id, Reader over the payload)."""
        while True:
            r = Reader(self.buf)
            try:
                n = r.varint()
                body = r.take(n)
                self.buf = r.rest()
                pr = Reader(body)
                return pr.varint(), pr
            except EOFError:
                chunk = self.s.recv(65536)
                if not chunk:
                    raise ConnectionError("server closed the connection")
                self.buf += chunk

    def handshake(self, next_state, host="localhost", port=25565):
        self.send(0, varint(47) + string(host) + struct.pack(">H", port) + varint(next_state))

    def close(self):
        self.s.close()


def status(host="127.0.0.1", port=25565):
    c = Conn(host, port)
    c.handshake(1)
    c.send(0)
    pid, r = c.recv()
    assert pid == 0, pid
    info = json.loads(r.string())
    c.send(1, struct.pack(">q", 0x0123456789ABCDEF))
    pid, r = c.recv()
    assert pid == 1, pid
    pong = struct.unpack(">q", r.take(8))[0]
    c.close()
    return info, pong


def login(name="Chicken", host="127.0.0.1", port=25565):
    """Handshake + Login Start; returns (conn, uuid, username) after Login Success."""
    c = Conn(host, port)
    c.handshake(2)
    c.send(0, string(name))
    pid, r = c.recv()
    assert pid == 2, "expected Login Success, got 0x%02x" % pid
    return c, r.string(), r.string()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 25565
    info, pong = status(port=port)
    print(json.dumps(info, indent=2, ensure_ascii=False))
    print("pong ok" if pong == 0x0123456789ABCDEF else "BAD PONG %x" % pong)
