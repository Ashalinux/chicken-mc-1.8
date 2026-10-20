# Host surface

This file lists everything `host.py` does and why the Chicken program can't do it. Anything not listed here, including all Minecraft logic, is in `server.chicken`.

## What the host does

| # | Host action | Why Chicken can't do it |
|---|---|---|
| 1 | Listens on TCP, accepts connections and reads/writes sockets. | Chicken has no I/O except one input value and one output value. |
| 2 | Runs the interpreter once per event: `open`, `data`, `tick` or `close`. | A Chicken program runs once and exits. It can't wait for anything. |
| 3 | Builds the input as a **list of byte values**: `[event] + state + received_bytes`. | Chicken has no "character to number" operation. It could never read the values of bytes passed as a string (see below). |
| 4 | Keeps one opaque `state` byte string per connection and passes it back on the next run. | Chicken has no memory between runs. |
| 5 | Reads the first two output bytes as a big-endian length `L`, keeps `out[:2+L]` as the new state, and sends `out[2+L:]` to the socket. | One run produces one output value, so the host needs some marker to tell "keep" from "send". This is the only part of the output the host reads. |
| 6 | Sends a `tick` event every second (`--tick`). | Chicken has no clock. The host only reports that time has passed; Chicken decides what to send. |
| 7 | Converts the output string to bytes (latin-1, code points 0–255). | This is just turning a string into bytes on the wire. The interpreter's normal `&#N;` output decoding does the actual work. |

## What the host does *not* do
- **No packet framing.** The host doesn't read VarInt lengths and doesn't buffer packets. It passes along whatever `read()` returns. Chicken parses the length prefixes. When a packet is incomplete, Chicken copies the unconsumed bytes to the end of its own state, and they come back in front of the next event's bytes. `tests/test_server.py::test_status_byte_by_byte` sends a status request one byte at a time to check this.
- **It never looks inside the state.** It only knows the state's length, from rule 5.
- **No Minecraft knowledge.** There are no packet IDs, connection phases or keep-alive logic in the host.

## Contract
```
input  = [event, *state, *received]        event: 0 open, 1 data, 2 tick, 3 close
                                            state is b"" before the first run
output = Lhi Llo <L bytes>  <bytes to send>
new state = Lhi Llo <L bytes>               (the header is part of the state)
```

## Why the input is a list and not a string
Chicken has `char` (number → `&#N;`) but no inverse, and it has no string comparison that could rebuild one. The esolangs spec says the input is "usually a string", so a list is allowed. `pick` from source 1 then returns plain numbers. The output side needs no help: Chicken builds `&#N;` entities, and the interpreter decodes them as specified.

## The interpreter as a library
The host imports `chicken.py` and parses `server.chicken` once at startup, so the 800 KB file isn't parsed again on every event. Execution itself is unchanged.
