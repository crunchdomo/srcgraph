# bench

Checks of srcgraph's answers against something srcgraph did not produce.

## untested_accuracy.py

How often is `srcgraph untested` right? The answer key is what `coverage` saw the project's own tests execute. Usage is in the script's docstring.

Results on 2026-11-21, srcgraph 0.2.0:

| | Flask (`49943b5`) | srcgraph itself |
|---|---|---|
| Functions judged | 322 | 99 |
| srcgraph says untested | 290 | 40 |
| ...and tests really never ran them | 16 (6%) | 2 (5%) |
| Functions the tests ran | 305 | 96 |
| ...that srcgraph had reached from a test | 31 (10%) | 58 (60%) |
| Functions with no symbol in the graph (nested, earlier `@overload`) | 55, of which 34 ran | 54, of which 49 ran |

So an `untested` row is wrong about nineteen times in twenty on both projects. The cause is the one `srcgraph callers` already prints: a method called on anything but `self` or a module leaves no edge. Tests mostly build an object and call methods on it (`app = Flask(...)`, `app.test_client()`), and a constructor call does not reach `__init__`.

Read with care: this judges one preset on two projects. Nothing here measures `importers`, `scope`, `domains` or any query that does not walk CALLS.
