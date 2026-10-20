"""chasm: a minimal assembler from readable pseudo-ops to Chicken.

DEVIATION NOTE: this is a build-time convenience only.  The program that runs
is the generated .chicken file; this tool never runs at serve time.

Syntax (one or more statements per line, separated by ';', '//' comments):
  label:                 define a label
  var a b c              allocate memory cells (above the working stack)
  array name SIZE        allocate SIZE consecutive cells; arrays come after vars
  const NAME VALUE       named constant
  macro NAME p1 p2 ... / endm   textual macro; '%%' in a body becomes a unique id

Instructions (all expand to plain Chicken opcodes):
  push N|NAME|&label     push number / const / var-or-array address / label address
  chicken add sub mul eq char      the native ops (sub/mul: below OP top)
  load                   top = mem[top]          (pick, source 0)
  in                     top = input[top]        (pick, source 1)
  store                  mem[top] = below; pops both   (peck)
  get V / set V          push mem[V] / pop into mem[V]
  inc V / dec V
  jmp L / jt L / jf L    jump always / if popped value truthy / falsy
  call L / ret           subroutine call via a call stack in memory (var __csp)
  dup drop swap          via scratch cells
  str "text"             push "&#b;&#b;..." for the UTF-8 bytes of text
  strlen "text"          push the byte length of text
  halt                   stop; the top of stack is the program's output
  raw N                  emit opcode N verbatim
"""
import ast
import re
import sys

STACK_MARGIN = 256          # cells kept free between the code and the variables
SMALL = 50                  # constants up to this are a single 'push' line
FIX_BASE = 200              # base used by fixed-size (relocatable) constants


class AsmError(Exception):
    pass


# ------------------------------------------------------------- tokenizing
def split_outside_quotes(text, sep):
    out, cur, q, i = [], [], False, 0
    while i < len(text):
        c = text[i]
        if q:
            cur.append(c)
            if c == "\\" and i + 1 < len(text):
                cur.append(text[i + 1]); i += 1
            elif c == '"':
                q = False
        elif c == '"':
            q = True; cur.append(c)
        elif text.startswith(sep, i):
            out.append("".join(cur)); cur = []
            i += len(sep)
            continue
        else:
            cur.append(c)
        i += 1
    out.append("".join(cur))
    return out


def tokens(stmt):
    return re.findall(r'"(?:\\.|[^"\\])*"|\S+', stmt)


def read_statements(path, seen=None):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    stmts = []
    for lineno, line in enumerate(text.split("\n"), 1):
        line = split_outside_quotes(line, "//")[0]
        for s in split_outside_quotes(line, ";"):
            t = tokens(s)
            if t:
                stmts.append((t, "%s:%d" % (path, lineno)))
    return stmts


# ------------------------------------------------------------ macro pass
def expand_macros(stmts):
    macros, out, counter = {}, [], [0]
    i = 0
    while i < len(stmts):
        t, where = stmts[i]
        if t[0] == "macro":
            name, params, body = t[1], t[2:], []
            i += 1
            while stmts[i][0][0] != "endm":
                body.append(stmts[i]); i += 1
            macros[name] = (params, body)
        else:
            out.append((t, where))
        i += 1

    def expand(stmt, depth):
        t, where = stmt
        if t[0] not in macros:
            return [stmt]
        if depth > 50:
            raise AsmError("%s: macro recursion too deep" % where)
        params, body = macros[t[0]]
        if len(t) - 1 != len(params):
            raise AsmError("%s: macro %s takes %d args" % (where, t[0], len(params)))
        counter[0] += 1
        uid = str(counter[0])
        sub = dict(zip(params, t[1:]))
        res = []
        for bt, _ in body:
            nt = [sub.get(x, x).replace("%%", "_m" + uid) for x in bt]
            res += expand((nt, where), depth + 1)
        return res

    final = []
    for s in out:
        final += expand(s, 0)
    return final


# ---------------------------------------------------------- number encoding
def enc(n):
    """Shortest-ish op sequence that pushes integer n."""
    if 0 <= n <= SMALL:
        return [n + 10]
    if n < 0:
        return [10] + enc(-n) + [3]
    q, r = divmod(n, SMALL)
    return enc(q) + [SMALL + 10, 4] + ([r + 10, 2] if r else [])


def enc_fixed(n):
    """Always 7 ops, so label addresses/offsets don't change code size."""
    neg = n < 0
    q, r = divmod(abs(n), FIX_BASE)
    if q > FIX_BASE:
        raise AsmError("program too large for fixed constants (%d)" % n)
    core = [q + 10, FIX_BASE + 10, 4, r + 10, 2]
    return ([10] + core + [3]) if neg else (core + [10, 2])


# ------------------------------------------------------------- assembling
def assemble(path):
    stmts = expand_macros(read_statements(path))
    consts, varnames, arrays = {}, [], []
    body = []
    for t, where in stmts:
        if t[0] == "var":
            varnames += t[1:]
        elif t[0] == "array":
            arrays.append((t[1], int(t[2], 0)))
        elif t[0] == "const":
            consts[t[1]] = int(t[2], 0)
        else:
            body.append((t, where))
    for v in ("__csp", "__t0", "__t1", "__drop"):
        varnames.append(v)
    arrays.insert(0, ("__cstk", 256))

    labels = {}
    var_base = 1000
    for _ in range(50):
        addrs = {}
        a = var_base
        for v in varnames:
            addrs[v] = a; a += 1
        for name, size in arrays:
            addrs[name] = a; a += size
        code, new_labels = [], {}
        ctx = dict(consts=consts, addrs=addrs, labels=labels)
        for t, where in body:
            if len(t) == 1 and t[0].endswith(":"):
                new_labels[t[0][:-1]] = len(code) + 2
                continue
            try:
                code += emit(t, len(code) + 2, ctx)
            except AsmError as e:
                raise AsmError("%s: %s" % (where, e))
            except (IndexError, ValueError) as e:
                raise AsmError("%s: bad statement %r (%s)" % (where, " ".join(t), e))
        new_labels["__end"] = len(code) + 2
        need = -(-(len(code) + 3 + STACK_MARGIN) // 1000) * 1000
        if new_labels == labels and need <= var_base:
            return code
        labels = new_labels
        var_base = max(var_base, need)
    raise AsmError("layout did not converge")


def value(tok, ctx):
    if tok.startswith("&"):
        return ctx["labels"].get(tok[1:], 0), True
    if re.fullmatch(r"-?(0x[0-9a-fA-F]+|\d+)", tok):
        return int(tok, 0), False
    if tok in ctx["consts"]:
        return ctx["consts"][tok], False
    if tok in ctx["addrs"]:
        return ctx["addrs"][tok], False
    raise AsmError("unknown name %r" % tok)


def lit_bytes(tok):
    if not (tok.startswith('"') and tok.endswith('"')):
        raise AsmError("expected string literal, got %r" % tok)
    b = ast.literal_eval(tok).encode("utf-8")
    if not b:
        raise AsmError("empty string literal")
    return b


NATIVE = {"chicken": [1], "add": [2], "sub": [3], "mul": [4], "eq": [5],
          "load": [6, 0], "in": [6, 1], "store": [7], "char": [9]}


def emit(t, at, ctx):
    op, args = t[0], t[1:]
    A = ctx["addrs"]

    def addr(name):
        if name not in A:
            raise AsmError("unknown variable %r" % name)
        return A[name]

    def jump_to(label, pre, at):
        # pre + enc_fixed(offset) + [8]; offset relative to ip after the 8
        if label not in ctx["labels"] and ctx["labels"]:
            raise AsmError("unknown label %r" % label)
        jaddr = at + len(pre) + 7
        return pre + enc_fixed(ctx["labels"].get(label, 0) - (jaddr + 1)) + [8]

    if op in NATIVE and not args:
        return list(NATIVE[op])
    if op == "raw":
        return [int(args[0], 0)]
    if op == "push":
        v, reloc = value(args[0], ctx)
        return enc_fixed(v) if reloc else enc(v)
    if op == "get":
        return enc(addr(args[0])) + [6, 0]
    if op == "set":
        return enc(addr(args[0])) + [7]
    if op in ("inc", "dec"):
        a = enc(addr(args[0]))
        return a + [6, 0, 11, 2 if op == "inc" else 3] + a + [7]
    if op == "jmp":
        return jump_to(args[0], [11], at)
    if op == "jt":
        return jump_to(args[0], [], at)
    if op == "jf":
        return jump_to(args[0], [10, 5], at)
    if op == "halt":
        return jump_to("__end", [11], at)
    if op == "dup":
        return enc(A["__t0"]) + [7] + enc(A["__t0"]) + [6, 0] + enc(A["__t0"]) + [6, 0]
    if op == "drop":
        return enc(A["__drop"]) + [7]
    if op == "swap":
        t0, t1 = enc(A["__t0"]), enc(A["__t1"])
        return t0 + [7] + t1 + [7] + t0 + [6, 0] + t1 + [6, 0]
    if op == "str":
        out = []
        for i, b in enumerate(lit_bytes(args[0])):
            out += [b + 10, 9] + ([2] if i else [])
        return out
    if op == "strlen":
        return enc(len(lit_bytes(args[0])))
    if op == "call":
        csp = enc(A["__csp"])
        # mem[csp] = return address ; csp += 1 ; jump
        head_len = 7 + len(csp) + 3 + len(csp) + 2 + 2 + len(csp) + 1
        jmp_len = 1 + 7 + 1
        ret = at + head_len + jmp_len
        head = enc_fixed(ret) + csp + [6, 0, 7] + csp + [6, 0, 11, 2] + csp + [7]
        assert len(head) == head_len
        return head + jump_to(args[0], [11], at + head_len)
    if op == "ret":
        csp = enc(A["__csp"])
        # csp -= 1 ; jump by (mem[csp] - ip_after_jump)
        pre = csp + [6, 0, 11, 3] + csp + [7] + [11] + csp + [6, 0, 6, 0]
        jaddr = at + len(pre) + 7 + 1
        return pre + enc_fixed(jaddr + 1) + [3, 8]
    raise AsmError("unknown op %r" % op)


def to_chicken(code):
    return "\n".join(" ".join(["chicken"] * n) for n in code)


def main(argv):
    if len(argv) != 3:
        print("usage: chasm.py IN.chasm OUT.chicken", file=sys.stderr)
        return 2
    code = assemble(argv[1])
    with open(argv[2], "w", encoding="utf-8", newline="\n") as f:
        f.write(to_chicken(code))
    print("%s: %d instructions" % (argv[2], len(code)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
