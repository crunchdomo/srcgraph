"""How often is `srcgraph untested` right? Judge it against what coverage saw the tests execute.

In the repository under test:

    coverage run -m pytest && coverage json                      # -> coverage.json
    srcgraph build --src src --src . && srcgraph untested --format json > untested.json
    srcgraph query --format json "MATCH (s:Symbol) WHERE s.kind IN ['function','method'] RETURN s.path AS path, s.line AS line" > symbols.json
    python path/to/bench/untested_accuracy.py coverage.json untested.json symbols.json --under src

The answer key comes from running the code, never from srcgraph, so srcgraph cannot mark its own homework.
"""
import argparse
import ast
import json
import sys
from pathlib import Path

DEFS = (ast.FunctionDef, ast.AsyncFunctionDef)


def functions(source: str) -> list[tuple[int, str, set[int]]]:
    """Every function as (def line, name, own statement lines). A nested function's body belongs to it alone."""
    out = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, DEFS):
            lines, stack = set(), list(node.body)
            while stack:
                inner = stack.pop()
                if isinstance(inner, ast.stmt):
                    lines.add(inner.lineno)
                if not isinstance(inner, DEFS):
                    stack.extend(ast.iter_child_nodes(inner))
            out.append((node.lineno, node.name, lines))
    return out


def score(funcs, executed, executable, untested):
    """Sort each function by srcgraph's claim and by whether a test ran it. Functions coverage cannot see are left out."""
    result = {"untested_but_ran": [], "untested_and_idle": [], "reached_and_ran": [], "reached_but_idle": []}
    for key, lines in sorted(funcs.items()):
        path = key[0]
        if not lines & executable.get(path, set()):
            continue
        ran = bool(lines & executed.get(path, set()))
        if key in untested:
            result["untested_but_ran" if ran else "untested_and_idle"].append(key)
        else:
            result["reached_and_ran" if ran else "reached_but_idle"].append(key)
    return result


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("coverage_json")
    p.add_argument("untested_json")
    p.add_argument("symbols_json", help="every function and method the graph holds, as path and line")
    p.add_argument("--under", default="", help="only judge files under this path prefix (leave tests out)")
    p.add_argument("--show", type=int, default=15, help="how many wrong 'untested' claims to list")
    args = p.parse_args(argv)

    files = json.loads(Path(args.coverage_json).read_text())["files"]
    executed = {f: set(d["executed_lines"]) for f, d in files.items()}
    executable = {f: set(d["executed_lines"]) | set(d["missing_lines"]) for f, d in files.items()}
    untested = {(row["path"], row["line"]) for row in json.loads(Path(args.untested_json).read_text())}
    known = {(row["path"], row["line"]) for row in json.loads(Path(args.symbols_json).read_text())}

    funcs, names = {}, {}
    for path in files:
        if path.startswith(args.under):
            for line, name, lines in functions(Path(path).read_text()):
                funcs[(path, line)], names[(path, line)] = lines, name

    absent = [k for k in funcs if k not in known]         # nested functions, earlier @overloads: no claim to judge
    absent_ran = sum(1 for k in absent if funcs[k] & executed.get(k[0], set()))
    r = score({k: v for k, v in funcs.items() if k in known}, executed, executable, untested)
    wrong, right_idle = len(r["untested_but_ran"]), len(r["untested_and_idle"])
    right_ran, over = len(r["reached_and_ran"]), len(r["reached_but_idle"])
    unmatched = sum(1 for key in untested if key[0].startswith(args.under) and key not in funcs)

    def pct(a, b):
        return f"{100 * a / b:.0f}%" if b else "n/a"

    print(f"functions judged: {wrong + right_idle + right_ran + over}")
    print(f"srcgraph says untested: {wrong + right_idle}, of which tests really never ran {right_idle} "
          f"({pct(right_idle, wrong + right_idle)} of the claims hold)")
    print(f"tests ran {wrong + right_ran} functions; srcgraph knew about {right_ran} ({pct(right_ran, wrong + right_ran)})")
    print(f"reached on paper but never run: {over}")
    print(f"functions the graph holds no symbol for: {len(absent)}, of which tests ran {absent_ran}")
    if unmatched:
        print(f"untested rows with no function at that line (classes, variables, or a line mismatch): {unmatched}")
    for path, line in r["untested_but_ran"][:args.show]:
        print(f"  wrong: {path}:{line} {names[(path, line)]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
