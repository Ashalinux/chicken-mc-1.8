"""Chicken interpreter, faithful to https://esolangs.org/wiki/Chicken and the
original reference implementation (torso.me/chicken, chicken-original.js).

Memory: one array `stack`
  stack[0]  -> the stack itself (by reference)
  stack[1]  -> the user input
  stack[2:] -> code (one cell per line = number of 'chicken' tokens),
               padded with a single 0 (EXIT); the working stack grows after it.
Values follow JavaScript semantics (weak typing), as the original does.
Output is the top of the stack, converted to a string and HTML-unescaped
(only numeric entities &#N; are produced by Chicken itself).
"""
import math
import re
import sys

UNDEFINED = None


class ChickenError(Exception):
    pass


# ---------------------------------------------------------------- parsing
def parse(source):
    ops = []
    for lineno, line in enumerate(source.split("\n"), 1):
        rest = line.replace("\r", "").replace(" ", "")
        n, rem = divmod(len(rest), 7)
        if rem or rest != "chicken" * n:
            raise ChickenError("Error on line %d: expected 'chicken'" % lineno)
        ops.append(n)
    return ops


# ---------------------------------------------------------- JS semantics
def js_num(v):
    if v is None:
        return math.nan
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        s = v.strip()
        if s == "":
            return 0
        try:
            return int(s, 0) if s.lower().startswith(("0x", "0o", "0b")) else int(s)
        except ValueError:
            try:
                return float(s)
            except ValueError:
                return math.nan
    if isinstance(v, list):
        return js_num(js_str(v))
    return math.nan


def js_str(v):
    if v is None:
        return "undefined"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        if math.isnan(v):
            return "NaN"
        if math.isinf(v):
            return "Infinity" if v > 0 else "-Infinity"
        if v.is_integer() and abs(v) < 1e21:
            return str(int(v))
        return repr(v)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, list):
        return ",".join("" if x is None else js_str(x) for x in v)
    return str(v)


def _norm(x):
    if isinstance(x, float) and x.is_integer() and abs(x) < 2 ** 53:
        return int(x)
    return x


def js_add(a, b):
    if isinstance(a, (str, list)) or isinstance(b, (str, list)):
        return js_str(a) + js_str(b)
    return _norm(js_num(a) + js_num(b))


def js_truthy(v):
    if v is None or v is False:
        return False
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v != 0 and not (isinstance(v, float) and math.isnan(v))
    if isinstance(v, str):
        return v != ""
    return True


def js_loose_eq(a, b):
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, list) and isinstance(b, list):
        return a is b
    if type(a) is type(b) or (isinstance(a, (int, float)) and isinstance(b, (int, float))
                              and not isinstance(a, bool) and not isinstance(b, bool)):
        return a == b
    if isinstance(a, list):
        a = js_str(a)
    if isinstance(b, list):
        b = js_str(b)
    if isinstance(a, str) and isinstance(b, str):
        return a == b
    return js_num(a) == js_num(b)


def js_index(container, key):
    """container[key] for the two things Chicken can index: arrays and strings."""
    if isinstance(key, float) and key.is_integer():
        key = int(key)
    if isinstance(key, str) and re.fullmatch(r"0|[1-9][0-9]*", key):
        key = int(key)
    if isinstance(container, (list, str)) and isinstance(key, int) and not isinstance(key, bool):
        return container[key] if 0 <= key < len(container) else UNDEFINED
    if isinstance(container, (list, str)) and key == "length":
        return len(container)
    return UNDEFINED


def html_unescape(s):
    """Decode numeric character references &#N; / &#xN; to code points.
    Deliberately minimal: chr(N) for every N (no HTML5 cp1252 remapping),
    so &#0;..&#255; round-trip to bytes via latin-1."""
    def rep(m):
        t = m.group(1)
        n = int(t[1:], 16) if t[:1] in "xX" else int(t)
        return chr(n) if 0 <= n <= 0x10FFFF else m.group(0)
    return re.sub(r"&#([0-9]+|[xX][0-9a-fA-F]+);", rep, s)


# -------------------------------------------------------------- machine
def run(ops, inp="", max_steps=None, trace=False):
    """Execute and return the raw top-of-stack value."""
    stack = [None, inp] + list(ops) + [0]
    stack[0] = stack
    sp = len(stack) - 1          # points at the EXIT pad, like the original
    ip = 2
    steps = 0

    def get(i):
        return stack[i] if 0 <= i < len(stack) else UNDEFINED

    def put(i, v):
        if i < 0:
            raise ChickenError("store to negative address %r" % i)
        if i >= len(stack):
            stack.extend([UNDEFINED] * (i + 1 - len(stack)))
        stack[i] = v

    while True:
        op = get(ip)
        ip += 1
        if not js_truthy(op):            # 0 / undefined: exit
            break
        if not isinstance(op, int) or isinstance(op, bool):
            op = js_num(op)
            if not (isinstance(op, (int, float)) and float(op).is_integer()):
                break
            op = int(op)
        steps += 1
        if max_steps and steps > max_steps:
            raise ChickenError("step limit exceeded")
        if trace:
            print(ip - 1, op, stack[max(2, sp - 5):sp + 1], file=sys.stderr)

        if op == 1:
            sp += 1; put(sp, "chicken")
        elif op == 2:
            b = get(sp); sp -= 1; put(sp, js_add(get(sp), b))
        elif op == 3:
            b = get(sp); sp -= 1; put(sp, _norm(js_num(get(sp)) - js_num(b)))
        elif op == 4:
            b = get(sp); sp -= 1; put(sp, _norm(js_num(get(sp)) * js_num(b)))
        elif op == 5:
            b = get(sp); sp -= 1; put(sp, js_loose_eq(b, get(sp)))
        elif op == 6:
            src = get(ip); ip += 1
            src_i = js_num(src)
            source = get(int(src_i)) if isinstance(src_i, (int, float)) and float(src_i).is_integer() else UNDEFINED
            put(sp, js_index(source, get(sp)))
        elif op == 7:
            addr = js_num(get(sp))
            val = get(sp - 1)
            sp -= 2
            put(int(addr), val)
        elif op == 8:
            off = get(sp)
            sp -= 1
            if js_truthy(get(sp)):
                ip += int(js_num(off))
            sp -= 1
        elif op == 9:
            put(sp, "&#" + js_str(get(sp)) + ";")
        else:
            sp += 1; put(sp, op - 10)
    return get(sp)


def run_output(ops, inp="", **kw):
    """Execute and return the program's output text (top of stack, unescaped)."""
    return html_unescape(js_str(run(ops, inp, **kw)))


def main(argv):
    if len(argv) < 2:
        print("usage: chicken.py PROGRAM.chicken [INPUT]", file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8") as f:
        ops = parse(f.read())
    inp = argv[2] if len(argv) > 2 else sys.stdin.read()
    sys.stdout.write(run_output(ops, inp))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
