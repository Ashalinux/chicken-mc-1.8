# Chicken Minecraft 1.8 server

A Minecraft 1.8 (protocol 47) server written in [Chicken](https://esolangs.org/wiki/Chicken). The world is a small flat map.

## Layout
- `chicken.py`: the Chicken interpreter.
- `tests/`: the interpreter test suite. `tests/programs/` holds the reference programs from the original distribution (quine, cat, hello world, 99 chickens).
- `tests/ref/chicken-original.js`: the original reference implementation, kept for comparison.

- `chasm.py`: the assembler (**a deviation from "pure Chicken"**, see below).
- `src/server.chasm`: the server source written in assembler pseudo-ops.
- `server.chicken`: the generated Chicken program, which is the program that actually runs.
- `host.py`: the TCP host. [HOST_SURFACE.md](HOST_SURFACE.md) lists exactly what it does.
- `tests/mcclient.py`: a small 1.8 protocol client used by the tests.

## Running the server
```
python chasm.py src/server.chasm server.chicken     # build
python host.py -v                                   # listen on 0.0.0.0:25565
python tests/mcclient.py                            # status + ping check
python -m unittest discover -s tests -v             # all tests
```

## The assembler (a deviation)
`chasm.py` turns pseudo-ops such as `push`, `get`/`set`, `jmp`/`jt`/`jf`, `call`/`ret`, `str` and macros into plain Chicken opcodes. It runs only at build time. Every pseudo-op expands into native Chicken instructions (`add`, `fox`, `pick`, `peck`, `fr`, `BBQ`, …), and nothing executes at runtime except `server.chicken`.

## Stage 1: the interpreter
```
python chicken.py tests/programs/hello_world.chicken ""
python -m unittest discover -s tests -v
```

### Spec decisions
These follow esolangs.org. Where the page is silent, the original `chicken-original.js` decides.

- **Memory.** Everything lives in one array: `[stack_ref, input, op1..opN, 0]`. Execution starts at index 2, and the working stack is pushed directly after the EXIT pad.
- **The EXIT pad can be overwritten.** A binary op that consumes the bottom stack value writes its result into the pad, and execution then continues into the data. The tests cover this.
- **Arithmetic.** `add`, `fox` and `rooster` use JavaScript semantics, so `"chicken"+3` gives `"chicken3"` and `"chicken"-3` gives `NaN`. Each operation is computed as *below op top*.
- **compare** uses JS loose `==`.
- **jump** pops the offset (top) and then the condition. If the condition is truthy, the offset is added to `ip`, which already points past the jump.
- **load (`pick`)** reads its source from the next code cell: `0` means the whole array and `1` means the input. It replaces the top of the stack with `source[top]`, or `undefined` if that cell doesn't exist.
- **store (`peck`)** does `mem[top] = below` and pops both values.
- **char (`BBQ`)** turns the top value `x` into the string `"&#x;"`.
- **Output** is the top of the stack converted to a string, with `&#N;` entities decoded. Each entity becomes `chr(N)`, without the HTML5 cp1252 remapping, so bytes 0–255 survive a round trip.

## What the server does (stages 2–5)
| Stage | Behavior, all inside `server.chicken` |
|---|---|
| 2 | **Server list ping.** Handshake, Status Response (custom MOTD, version "1.8", 0/20 players) and Ping/Pong. Packets may arrive split across any number of reads. |
| 3 | **Offline login.** Login Start → Login Success. The UUID is the name's bytes in hex, zero-padded (see deviations). |
| 4 | **Joining the world.** Join Game (creative, `flat`), Spawn Position, 7×7 chunks (bedrock, dirt, dirt, grass, built by string doubling), Player Position And Look. |
| 5 | **Staying connected.** A Keep Alive goes out every 5 ticks (one tick per second from the host). Chat is echoed back as `<name> message`, with `"` and `\` escaped. |
| extra | **The player is a chicken.** The player is made invisible and a chicken rides on it, visible in F5. |

## Deviations / limits
- **Offline UUID.** Vanilla uses MD5(`OfflinePlayer:`+name). Chicken has no bitwise operations or division, so MD5 is impractical. The UUID is derived from the name's bytes instead.
- **Player count** is fixed, because each run only sees its own connection.
- **Chicken avatar.** 1.8 can't change the local player's model, so the avatar is a riding chicken, which sits about one block higher than the player's feet.
- **The assembler** is a build-time deviation (see above).
