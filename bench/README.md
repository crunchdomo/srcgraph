# bench

Checks of srcgraph's answers against something srcgraph did not produce.

## untested_accuracy.py

How often is `srcgraph untested` right? The answer key is what `coverage` saw the project's own tests execute. Usage is in the script's docstring.

Results on 2026-11-21, Flask at `49943b5` and srcgraph itself:

| | Flask, 0.2.0 | Flask, typed receivers | srcgraph, 0.2.0 | srcgraph, typed receivers |
|---|---|---|---|---|
| Functions the tests ran | 305 | 305 | 96 | 97 |
| ...that srcgraph had reached from a test | 31 (10%) | 112 (37%) | 58 (60%) | 80 (82%) |
| srcgraph says untested | 290 | 209 | 40 | 19 |
| ...and tests really never ran them | 16 (6%) | 16 (8%) | 2 (5%) | 2 (11%) |

"Typed receivers" is the change that follows an object to its class when the code says what it is: `x = A()`, an annotation, a function's return, a pytest fixture, `self.m()` and `super().m()` through base classes, and a class call reaching `__init__`.

How to read it:

- An `untested` row is still usually wrong. Do not use `untested` as a coverage report; `coverage` answers that question exactly.
- What is left on Flask is mostly dispatch the code never spells out: the test client calls into werkzeug, which calls `app.__call__`, and everything below it (`wsgi_app`, `dispatch_request`, the handlers) hangs off that one edge. No static reading of Flask's own code shows it.
- This judges one preset on two projects. Nothing here measures `importers`, `scope`, `domains` or any query that does not walk CALLS.
