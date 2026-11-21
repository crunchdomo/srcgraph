"""The scoring behind bench/untested_accuracy.py: which lines are a function's own, and how a claim is judged."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "untested_accuracy", Path(__file__).parent.parent / "bench" / "untested_accuracy.py")
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)

SOURCE = '''\
def outer(x):
    """Doc."""
    y = x + 1
    def inner():
        return y
    return inner

class K:
    def method(self):
        return 1

def stub(): ...
'''


def test_a_function_owns_its_statements_but_not_a_nested_functions_body():
    found = {name: lines for _, name, lines in bench.functions(SOURCE)}
    assert found["outer"] == {2, 3, 4, 6}        # line 4 is inner's def, line 5 is inner's body
    assert found["inner"] == {5}
    assert found["method"] == {10}


def test_functions_are_keyed_by_their_def_line():
    assert {(line, name) for line, name, _ in bench.functions(SOURCE)} == {
        (1, "outer"), (4, "inner"), (9, "method"), (12, "stub")}


def test_score_judges_each_function_against_what_ran():
    funcs = {("a.py", 1): {2}, ("a.py", 5): {6}, ("a.py", 9): {10}, ("a.py", 13): {14}, ("a.py", 17): {18}}
    executed = {"a.py": {2, 6}}
    executable = {"a.py": {2, 6, 10, 14}}           # line 18 is not executable: that function cannot be judged
    untested = {("a.py", 1), ("a.py", 9)}
    assert bench.score(funcs, executed, executable, untested) == {
        "untested_but_ran": [("a.py", 1)],          # srcgraph was wrong: a test did run it
        "untested_and_idle": [("a.py", 9)],         # srcgraph was right
        "reached_and_ran": [("a.py", 5)],           # srcgraph was right
        "reached_but_idle": [("a.py", 13)],         # a path exists that the tests never took
    }
