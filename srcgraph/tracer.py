"""Run one expression under sys.settrace inside the target repo's interpreter.

Launched by `srcgraph trace` as a plain script, so it must stay stdlib-only: the target's
interpreter need not have srcgraph installed. Emits one JSON event per line to the events file.
"""
import importlib
import json
import os
import sys

LIMIT = 60


def short(value) -> str:
    try:
        r = repr(value)
    except Exception:  # a repr that raises is still a value worth showing
        r = f"<{type(value).__name__}>"
    return r if len(r) <= LIMIT else r[: LIMIT - 1] + "…"


def main(table_path: str, events_path: str, expr: str) -> int:
    table = json.load(open(table_path))   # abs file -> name -> [[def line, qualified], ...] sorted by line
    events = open(events_path, "w")   # closed in the finally: the traceback cycle can keep it unflushed at exit
    known: dict = {}   # code object -> qualified or None
    raised: set = set()   # ids of frames unwinding with an exception

    def emit(**ev):
        events.write(json.dumps(ev) + "\n")

    def resolve(code):
        if code not in known:
            names = table.get(os.path.abspath(code.co_filename), {}).get(code.co_name) or []
            # a decorated def's co_firstlineno is the first decorator line, so take the first def at or after it
            known[code] = next((q for line, q in names if line >= code.co_firstlineno), None)
        return known[code]

    def depth(frame) -> int:
        d, f = 0, frame.f_back
        while f is not None:
            d += resolve(f.f_code) is not None
            f = f.f_back
        return d

    def local(frame, event, arg):
        if event == "exception":
            raised.add(id(frame))
            emit(ev="exc", depth=depth(frame) + 1, text=f"{arg[0].__name__}: {arg[1]}")
        elif event == "return" and id(frame) not in raised:
            emit(ev="return", depth=depth(frame) + 1, value=short(arg))
        # ponytail: a frame that catches an exception and returns normally shows no return line; track exc_info if it matters
        return local

    def tracer(frame, event, arg):
        frame.f_trace_lines = False
        if resolve(frame.f_code) is None:
            return None
        code = frame.f_code
        names = code.co_varnames[: code.co_argcount + code.co_kwonlyargcount]
        emit(ev="call", sym=resolve(code), depth=depth(frame), args={n: short(frame.f_locals[n]) for n in names if n in frame.f_locals})
        return local

    sys.path.insert(0, os.getcwd())
    parts = expr.split("(", 1)[0].strip().split(".")
    for i in range(len(parts), 0, -1):   # import the longest importable prefix before tracing starts
        try:
            importlib.import_module(".".join(parts[:i]))
            break
        except ImportError:
            continue
    ns = {parts[0]: sys.modules[parts[0]]} if parts[0] in sys.modules else {}
    sys.settrace(tracer)
    try:
        value = eval(expr, ns)
    except Exception as exc:
        emit(ev="exc", depth=0, text=f"{type(exc).__name__}: {exc}")
        return 1
    else:
        emit(ev="return", depth=0, value=short(value))
        return 0
    finally:
        sys.settrace(None)
        events.close()


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:4]))
