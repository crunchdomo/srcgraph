# bench

Checks of srcgraph's answers against something srcgraph did not produce.

## untested_accuracy.py

How often is `srcgraph untested` right? The answer key is what `coverage` saw the project's own tests execute. Usage is in the script's docstring.

Results on 2026-11-21, Flask at `49943b5` and srcgraph itself:

| | Flask | srcgraph itself |
|---|---|---|
| Functions the tests ran | 305 | 97 |
| Reached from a test, 0.2.0 | 31 (10%) | 58 (60%) |
| ...with typed receivers | 112 (37%) | 80 (82%) |
| ...with attributes, `with` and method results | 131 (43%) | 87 (90%) |
| Wrong "untested" rows, 0.2.0 | 274 | 38 |
| ...after both changes | 174 | 10 |

"Typed receivers" follows an object to its class when the code says what it is: `x = A()`, an annotation, a function's return, a pytest fixture, `self.m()` and `super().m()` through base classes, and a class call reaching `__init__`. The second change adds `self.attr.m()`, the name a `with` binds, and what a method gives back.

How to read it:

- An `untested` row is still usually wrong. Do not use `untested` as a coverage report; `coverage` answers that question exactly.
- What is left on Flask is mostly dispatch the code never spells out: the test client calls into werkzeug, which calls `app.__call__`, and everything below it (`wsgi_app`, `dispatch_request`, the handlers) hangs off that one edge. No static reading of Flask's own code shows it.
- An attribute or parameter annotated with a base class reaches the base's method, not a subclass's override: `json: JSONProvider` then `self.json.dumps()` lands on `JSONProvider.dumps` while `DefaultJSONProvider.dumps` is what runs.
- Functions defined inside functions have no symbol at all, and tests ran most of them (34 of 55 on Flask, 58 of 63 here).
- This judges one preset on two projects. Nothing here measures `importers`, `scope`, `domains` or any query that does not walk CALLS.
