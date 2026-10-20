"""Minimal host: moves bytes between TCP sockets and the Chicken program.

It knows nothing about Minecraft.  Per connection it keeps one opaque byte
string (`state`) that the Chicken program hands back on every run.
See HOST_SURFACE.md for the exact contract.
"""
import argparse
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from chicken import parse, run_output  # noqa: E402

OPEN, DATA, TICK, CLOSE = 0, 1, 2, 3


class Program:
    def __init__(self, path, verbose=False):
        with open(path, encoding="utf-8") as f:
            self.ops = parse(f.read())     # parsed once, executed per event
        self.verbose = verbose

    def step(self, event, state, data=b""):
        """Run the program once. Returns (new_state, bytes_to_send)."""
        t = time.perf_counter()
        out = run_output(self.ops, [event] + list(state) + list(data)).encode("latin-1")
        # The only thing the host reads from the output: a 2-byte length
        # telling it which prefix to keep as state.
        keep = 2 + (out[0] << 8 | out[1])
        if self.verbose:
            print("event %d in=%d out=%d (%.0f ms)" % (
                event, len(data), len(out) - keep, (time.perf_counter() - t) * 1000), file=sys.stderr)
        return out[:keep], out[keep:]


async def handle(program, tick_seconds, reader, writer):
    state = b""
    lock = asyncio.Lock()
    loop = asyncio.get_running_loop()

    async def fire(event, data=b""):
        nonlocal state
        async with lock:
            state, reply = await loop.run_in_executor(None, program.step, event, state, data)
            if reply and not writer.is_closing():
                writer.write(reply)
                await writer.drain()

    async def ticker():
        while True:
            await asyncio.sleep(tick_seconds)
            await fire(TICK)

    await fire(OPEN)
    tick_task = asyncio.create_task(ticker())
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            await fire(DATA, data)
    except (ConnectionError, OSError):
        pass
    finally:
        tick_task.cancel()
        try:
            await fire(CLOSE)
        except (ConnectionError, OSError):
            pass
        writer.close()


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--program", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "server.chicken"))
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=25565)
    ap.add_argument("--tick", type=float, default=1.0, help="seconds between tick events")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    program = Program(args.program, args.verbose)
    server = await asyncio.start_server(
        lambda r, w: handle(program, args.tick, r, w), args.host, args.port)
    print("listening on %s:%d" % (args.host, args.port), file=sys.stderr)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
