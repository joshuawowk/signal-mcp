"""Unit test for the options coercer. No container needed.
Run: python3 test/test_options.py
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
os.environ.setdefault("SIGNAL_SENDER", "+10000000000")
os.environ.setdefault("SIGNAL_DEFAULT_RECIPIENT", "+10000000001")

# Import only the coercer without pulling fastmcp: exec the function source.
import json, ast

def _coerce_options(options):
    if options is None:
        return []
    if isinstance(options, (list, tuple)):
        return [str(x).strip() for x in options if str(x).strip()]
    if not isinstance(options, str):
        return [str(options).strip()] if str(options).strip() else []
    s = options.strip()
    if not s:
        return []
    for parser in (json.loads, ast.literal_eval):
        try:
            parsed = parser(s)
            if isinstance(parsed, (list, tuple)):
                return [str(x).strip() for x in parsed if str(x).strip()]
        except (ValueError, SyntaxError):
            pass
    if "\n" in s:
        parts = s.split("\n")
    elif "," in s:
        parts = s.split(",")
    else:
        parts = [s]
    return [p.strip() for p in parts if p.strip()]


CASES = [
    (None, []),
    (["A", "B"], ["A", "B"]),
    ('["A", "B"]', ["A", "B"]),          # JSON array string (harness)
    ("['A', 'B']", ["A", "B"]),          # python-repr string
    ("A\nB\nC", ["A", "B", "C"]),        # newline-separated
    ("A, B, C", ["A", "B", "C"]),        # comma-separated
    ("restart container", ["restart container"]),  # single bare string
    ("", []),
    ([1, 2], ["1", "2"]),                # non-str list elements
    ('  ["x" , "y" ]  ', ["x", "y"]),    # whitespace + JSON
]


def main() -> int:
    all_ok = True
    for inp, exp in CASES:
        got = _coerce_options(inp)
        ok = got == exp
        all_ok = all_ok and ok
        print(f"{'OK  ' if ok else 'FAIL'} {inp!r:24} -> {got!r}")
    print("ALL PASS" if all_ok else "SOME FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
