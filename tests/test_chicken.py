import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from chicken import parse, run, run_output, ChickenError  # noqa: E402


def src(*ops):
    """Raw opcode list -> Chicken source text (test helper only)."""
    return "\n".join(" ".join(["chicken"] * n) for n in ops)


def _read(p):
    with open(p, encoding="utf-8", newline="") as f:
        return f.read()


def push(n):
    return n + 10


def hello_ops(text):
    ops = []
    for i, ch in enumerate(text):
        ops += [push(ord(ch)), 9]
        if i:
            ops.append(2)
    return ops


class ParseTests(unittest.TestCase):
    def test_counts(self):
        self.assertEqual(parse("chicken\nchicken chicken\n\nchicken chicken chicken"), [1, 2, 0, 3])

    def test_trailing_newline_is_an_exit(self):
        self.assertEqual(parse("chicken\n"), [1, 0])

    def test_crlf(self):
        self.assertEqual(parse("chicken chicken\r\nchicken\r\n"), [2, 1, 0])

    def test_rejects_other_words(self):
        with self.assertRaises(ChickenError):
            parse("chicken egg")


class ProgramTests(unittest.TestCase):
    def out(self, text, inp=""):
        return run_output(parse(text), inp)

    def test_chicken_quine(self):
        # one 'chicken' pushes "chicken", which is then printed
        self.assertEqual(self.out("chicken"), "chicken")

    def test_empty_program_outputs_undefined_pad(self):
        # sp starts on the EXIT pad (0), exactly like the original
        self.assertEqual(self.out(""), "0")

    def test_cat(self):
        # push 1 ; load from stack[0] (the stack itself) -> stack[1] = input
        cat = src(push(1), 6, 0)
        self.assertEqual(self.out(cat, "Hello, chicken!"), "Hello, chicken!")
        self.assertEqual(self.out(cat, ""), "")

    def test_input_char(self):
        # push 2 ; load from source 1 (input) -> input[2]
        self.assertEqual(self.out(src(push(2), 6, 1), "abcd"), "c")

    def test_hello_world(self):
        self.assertEqual(self.out(src(*hello_ops("Hello world"))), "Hello world")

    def test_arith_order(self):
        self.assertEqual(self.out(src(push(7), push(3), 3)), "4")   # below - top
        self.assertEqual(self.out(src(push(7), push(3), 4)), "21")
        self.assertEqual(self.out(src(push(7), push(3), 2)), "10")

    def test_js_add_coercion(self):
        self.assertEqual(self.out(src(1, push(3), 2)), "chicken3")
        self.assertEqual(self.out(src(push(3), 1, 2)), "3chicken")
        self.assertEqual(self.out(src(1, push(3), 3)), "NaN")

    def test_compare(self):
        self.assertEqual(self.out(src(push(5), push(5), 5)), "true")
        self.assertEqual(self.out(src(push(5), push(4), 5)), "false")
        self.assertEqual(self.out(src(1, 1, 5)), "true")

    def test_char(self):
        self.assertEqual(run(parse(src(push(65), 9))), "&#65;")
        self.assertEqual(self.out(src(push(65), 9)), "A")

    def test_jump_taken_and_not_taken(self):
        # cond off jump ; push 1 ; push 2   -- taken skips 'push 1'
        # a leading 'push 0' keeps the add from landing on the EXIT pad (see below)
        self.assertEqual(self.out(src(push(0), push(1), push(1), 8, push(1), push(2), 2)), "2")
        self.assertEqual(self.out(src(push(0), push(0), push(1), 8, push(1), push(2), 2)), "3")

    def test_popping_onto_exit_pad_overwrites_it(self):
        # Quirk: the working stack begins *on* the EXIT pad, so a binary op that
        # consumes the bottom value writes its result into the pad and execution
        # continues into the data cells.  Same in the original implementation.
        self.assertEqual(self.out(src(push(1), push(1), 8, push(1), push(2), 2)), "chicken")

    def test_store_and_load_scratch(self):
        # store 42 at address 100, then load it back via stack[0][100]
        p = src(push(42), push(100), 7, push(100), 6, 0)
        self.assertEqual(self.out(p), "42")

    def test_self_modifying_code(self):
        # overwrite the cell after the store with 'push 9' (19), it then executes
        # cells: 2:push19 3:push7 4:store 5:<0, becomes 19>
        p = src(push(19), push(5), 7, 0)
        self.assertEqual(self.out(p), "9")

    def test_countdown_loop(self):
        # sum = 0; n = 5; loop: sum += n; n -= 1; if n: goto loop
        # scratch: [200]=sum [201]=n
        ops = [push(0), push(200), 7, push(5), push(201), 7]
        loop = len(ops)
        body = [push(200), 6, 0, push(201), 6, 0, 2, push(200), 7,
                push(201), 6, 0, push(1), 3, push(201), 7,
                push(201), 6, 0]
        ops += body
        # jump back; the offset is relative to ip *after* the jump opcode and
        # negative numbers have to be built as 0 - k
        jump_at = len(ops) + 3
        ops += [push(0), push(jump_at + 1 - loop), 3, 8]
        ops += [push(200), 6, 0]
        self.assertEqual(self.out(src(*ops)), "15")


class ReferenceExamples(unittest.TestCase):
    """Programs from the esolangs.org page / original distribution."""

    def test_example_chicken_files(self):
        d = os.path.join(os.path.dirname(__file__), "programs")
        for name in sorted(os.listdir(d)):
            if not name.endswith(".chicken"):
                continue
            base = os.path.join(d, name[:-8])
            with open(base + ".chicken", encoding="utf-8") as f:
                ops = parse(f.read())
            inp = _read(base + ".in") if os.path.exists(base + ".in") else ""
            want = _read(base + ".out")
            with self.subTest(name=name):
                self.assertEqual(run_output(ops, inp), want)


if __name__ == "__main__":
    unittest.main()
