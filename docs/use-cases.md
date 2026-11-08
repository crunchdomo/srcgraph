# Use cases, on three public repos

Flask, Requests and FastAPI, cloned at their main branches on 2026-11-08 and scanned with the srcgraph
at this commit. Commands are as run; output is as printed, cut with `…` where a table was longer.
Every preset here prints candidates with an error bar, not verdicts: the last section says what the
graph still cannot see, and what it got wrong on the first pass before four fixes went in.

| repo | build | units / symbols / syntax nodes | wall time |
|---|---|---|---|
| pallets/flask | `srcgraph build --src src --src tests` | 145 / 1555 / 80833 | 5 s |
| psf/requests | `srcgraph build --src src --src .` | 105 / 1147 / 58098 | 4 s |
| fastapi/fastapi | `srcgraph build --exclude docs_src --exclude docs --exclude scripts` | 948 / 7202 / 667023 | 41 s |

Requests keeps `tests/` as a package (`tests/__init__.py`, `from tests.utils import …`), so its tests
are scanned from the repo root and `src/` is the second root; a file under both roots is scanned once,
under the more specific one.

## 1. First week on a codebase (Flask)

The two questions a newcomer has are "what does the most" and "what must I not break". `hubs`
answers both: fan-out is the first, fan-in the second.

```
$ srcgraph hubs
node                              fan_in  fan_out
flask.app.Flask                   125     5
flask.blueprints.Blueprint        56      1
src/flask/cli.py                  6       41
src/flask/__init__.py             35      10
src/flask/app.py                  6       38
src/flask/sansio/app.py           7       28
flask.templating.render_template  33      1
src/flask/globals.py              17      15
…
```

`cli.py` and `app.py` do the most; `Flask`, `Blueprint` and `render_template` are what everything
else holds on to, most of it the test suite. The one directory with its own README is
`src/flask/sansio/`, and the README states a rule: the code there may do no IO and may not use the
Flask globals, so that Quart can reuse it. `domains` is how you check a rule like that against the
code:

```
$ srcgraph domains sansio='src/flask/sansio/**' json='src/flask/json/**' cli='src/flask/cli.py' app='src/flask/app.py' blueprints='src/flask/blueprints.py'
from        to      edges
cli         app     16
blueprints  sansio  5
sansio      json    5
app         cli     4
json        sansio  3
app         sansio  2
blueprints  cli     2
== crossings (37)
…

$ srcgraph domains --no-typing sansio='src/flask/sansio/**' json='src/flask/json/**' cli='src/flask/cli.py' app='src/flask/app.py' blueprints='src/flask/blueprints.py'
from        to      edges
blueprints  sansio  5
sansio      json    5
app         cli     4
app         sansio  2
blueprints  cli     2
== crossings (18)
from    to      source                    label     target                           line
app     cli     flask.app.Flask.__init__  CALLS     flask.cli.AppGroup               338
app     cli     flask.app.Flask.run       CALLS     flask.cli.load_dotenv            719
app     sansio  flask.app.Flask           INHERITS  flask.sansio.app.App             108
sansio  json    flask.sansio.app.App      REFERENCES flask.json.provider.JSONProvider 228
sansio  json    src/flask/sansio/app.py   IMPORTS   flask.json.provider              27
…
```

Read the two tables together. The first says `cli` and `app` depend on each other, and so do
`json` and `sansio`. The second drops every import made under `if TYPE_CHECKING` and every
annotation that uses one, and both cycles are gone: the 16 `cli → app` edges are `Flask` in type
hints, the 3 `json → sansio` edges are `App` in the provider's annotations. At runtime the
dependencies run one way, `app → cli` and `sansio → json`, which is what the sans-IO rule needs.
Nothing under `sansio` reaches `flask.globals`, so the README's rule holds at one hop. `scope`
gives the full outbound list, which is where the second hop would start, with the guard column
saying which wires are typing-only:

```
$ srcgraph scope 'src/flask/sansio/**' --no-tests
src/flask/sansio/app.py
src/flask/sansio/blueprints.py
src/flask/sansio/scaffold.py
== internal (121)
…
== outbound (130)
source                        label       target                                 line  guard
flask.sansio.app.App          REFERENCES  flask.templating.Environment           170
flask.sansio.app.App          REFERENCES  flask.testing.FlaskClient              267   typing
flask.sansio.app.App          REFERENCES  flask.json.provider.DefaultJSONProvider 228
src/flask/sansio/app.py       IMPORTS     flask.helpers                          25
src/flask/sansio/app.py       IMPORTS     flask.templating                       30
src/flask/sansio/app.py       IMPORTS     flask.testing                          40    typing
…
```

Six of the 130 outbound wires are typing (`--no-typing` leaves 124). The header shows the three
files have no doc line: nothing at the top of any of them says what it is for, which is a fair first
pull request.

When the question is one function, `callers` prints each call with the condition it fires under:

```
$ srcgraph callers _endpoint_from_view_func
caller                                               line  target                                         guard
flask.sansio.app.App.add_url_rule                    615   flask.sansio.scaffold._endpoint_from_view_func  if@614
flask.sansio.blueprints.BlueprintSetupState.add_url_rule  105  flask.sansio.scaffold._endpoint_from_view_func  if@104
```

## 2. Before you change something (Requests)

The change is "touch `Session.request`". Who is affected, and what else is in the room?

```
$ srcgraph callers Session.request
caller                            line  target                              guard
requests.sessions.Session.delete  750   requests.sessions.Session.request
requests.sessions.Session.get     671   requests.sessions.Session.request
requests.sessions.Session.head    693   requests.sessions.Session.request
requests.sessions.Session.options 682   requests.sessions.Session.request
requests.sessions.Session.patch   740   requests.sessions.Session.request
requests.sessions.Session.post    712   requests.sessions.Session.request
requests.sessions.Session.put     726   requests.sessions.Session.request
```

Seven verb methods, all `self.request(...)`, which the graph resolves. The module-level `get`,
`post` and friends in `api.py` go through `requests.api.request` instead, which calls
`Session.request` on a `with sessions.Session() as session:` receiver; that call is one the graph
cannot see (a method on a local variable has no type), so `callers` is a lower bound here.

A dependency bump is the other common change. `importers` is the list of files to open:

```
$ srcgraph importers urllib3
importer                   line  module
src/requests/__init__.py   45    urllib3
src/requests/adapters.py   17    urllib3.exceptions
src/requests/adapters.py   31    urllib3.poolmanager
src/requests/adapters.py   34    urllib3.util.retry
src/requests/adapters.py   63    urllib3.contrib.socks
src/requests/compat.py     22    urllib3
src/requests/exceptions.py 12    urllib3.exceptions
src/requests/help.py       10    urllib3
src/requests/models.py     34    urllib3.fields
src/requests/utils.py      33    urllib3.util
tests/test_requests.py     16    urllib3
…
```

`adapters.py` has twelve of the lines; it is the urllib3 boundary. And the module matrix says
where the weight of the package sits:

```
$ srcgraph domains api='src/requests/api.py' sessions='src/requests/sessions.py' adapters='src/requests/adapters.py' models='src/requests/models.py' auth='src/requests/auth.py' cookies='src/requests/cookies.py' utils='src/requests/utils.py'
from      to       edges
sessions  models   29
adapters  models   14
models    utils    14
sessions  cookies  14
sessions  utils    13
auth      models   12
models    cookies  12
…
```

Everything leans on `models`. `hubs` agrees, and adds the view from the outside: `requests.api.get`
(fan-in 70), `Request` (68) and `Session` (58) are what the test suite holds on to, `PreparedRequest`
(36) and `Response` (46) are what the package itself does.

## 3. Who leans on whom (FastAPI)

FastAPI has recognisable bounded contexts in one package: routing, dependency injection, security,
OpenAPI generation, parameter declarations, the application object. Name them and the coupling is a
table:

```
$ srcgraph domains routing='fastapi/routing.py' deps='fastapi/dependencies/**' security='fastapi/security/**' openapi='fastapi/openapi/**' params='fastapi/param*.py' app='fastapi/applications.py'
from      to        edges
deps      params    39
routing   deps      29
routing   params    26
security  openapi   23
app       routing   20
openapi   routing   17
params    openapi   17
openapi   deps      16
app       params    15
security  params    13
deps      security  8
app       openapi   6
openapi   params    2
== crossings (231)
…
```

`--no-typing` gives the same table: none of this coupling is annotations. Reading it: `openapi`
depends on `routing` (17 edges, all `REFERENCES` to `APIRoute`, `RouteContext` and `_APIRouteLike`
from `openapi/utils.py`) and `routing` never depends back, so OpenAPI generation is downstream of
routing, as it should be. `security → openapi` is 23 edges because every security class builds its
own OpenAPI model in `__init__` (`fastapi.security.http.HTTPBase.__init__ CALLS
fastapi.openapi.models.HTTPBase`, line 80); that is by design, and now it is a stated fact rather
than something you discover in a diff review. The `starlette` boundary is two files:

```
$ srcgraph query "MATCH (u:Unit)-[r:IMPORTS]->(v:Unit {kind:'external'}) WHERE v.name STARTS WITH 'starlette' AND NOT u.path STARTS WITH 'tests' RETURN u.path, count(r) ORDER BY count(r) DESC LIMIT 5"
u.path                         count
fastapi/routing.py             13
fastapi/applications.py        11
fastapi/datastructures.py      7
fastapi/responses.py           7
fastapi/dependencies/utils.py  6
```

And a `callers` on the dependency resolver shows its recursion and the branch it recurses under:

```
$ srcgraph callers get_dependant
caller                                                          line  target                                     guard
fastapi.dependencies.utils.get_dependant                        350   fastapi.dependencies.utils.get_dependant   if@330
fastapi.dependencies.utils.get_parameterless_sub_dependant      151   fastapi.dependencies.utils.get_dependant
fastapi.dependencies.utils.solve_dependencies                   689   fastapi.dependencies.utils.get_dependant   if@680
fastapi.routing._build_dependant_with_parameterless_dependencies 914  fastapi.dependencies.utils.get_dependant
```

## 4. Tidying, and the error bar

`dead` and `untested` are the tidy-up presets. On a library their input is mostly the test suite,
because nothing else calls the public API.

| repo | `dead` rows | `untested` rows |
|---|---|---|
| flask | 6 | 343 |
| requests | 22 | 235 |
| fastapi | 4 | 401 |

```
$ srcgraph dead          # flask
symbol                                   path                         line
flask.app._make_timedelta                src/flask/app.py             72
flask.app.Flask.open_instance_resource   src/flask/app.py             445
flask.cli._path_is_ancestor              src/flask/cli.py             691
flask.ctx.has_app_context                src/flask/ctx.py             235
flask.debughelpers.UnexpectedUnicodeError src/flask/debughelpers.py   17
flask.sessions.NullSession._fail         src/flask/sessions.py        89

$ srcgraph dead          # fastapi
symbol                                   path                         line
fastapi.encoders.Color                   fastapi/encoders.py          39
fastapi.encoders.PyExtraColor            fastapi/encoders.py          48
fastapi.openapi.models.EmailStr          fastapi/openapi/models.py    23
fastapi.openapi.utils.generate_operation_id fastapi/openapi/utils.py  258
```

Six rows on Flask, and they read like a to-do list: `_make_timedelta` in `app.py` duplicates the one
in `sansio/app.py`, nothing calls `_path_is_ancestor`, `has_app_context` and
`open_instance_resource` are public API without a test. The FastAPI rows are the pydantic-version
fallbacks (`Color`, `EmailStr`) and one helper. On Requests the 22 rows are `MockRequest`'s
cookiejar hooks (duck-typed for `http.cookiejar`, so no edge can reach them), the `options` and
`delete` verbs that no test exercises, and a run of `utils` helpers kept for compatibility.

What the graph still cannot see, and so what these lists can be wrong about:

- A method called on anything but `self` or a module. There is no type information, so
  `session.request(...)` on a local leaves no edge. The empty answers say so:
  `no callers of X (896 of 1801 calls resolved; a method called on anything but self or a module
  leaves no edge, so it can look uncalled)`.
- Names looked up with `getattr(module, name)`. The module gets a `REFERENCES` edge and its public
  functions are kept out of `dead`, but nothing says which one is called.
- An `__all__` or a registry built at runtime.

The first scan of these repos also found four things the graph got wrong rather than could not see,
all fixed since: `from fastapi import FastAPI` resolved to the package instead of the class, which
put `FastAPI`, `Depends`, `url_for` and `requests.api.get` in `dead` (32 rows on FastAPI, now 4);
`--src src --src .` crashed on the file scanned twice; `callers Session.request` found nothing
without the module path; and a `TYPE_CHECKING` import looked like a runtime dependency.

## 5. As a library: one rule in CI

Everything above is a dict away in Python. The sans-IO rule from section 1 as a check, on the
runtime coupling only:

```python
import srcgraph

g = srcgraph.load(".", src=["src", "tests"])
crossings = g.domains({"sansio": "src/flask/sansio/**", "globals": "src/flask/globals.py"}, no_typing=True)["crossings"]
leaks = [c for c in crossings if c["from"] == "sansio"]
assert not leaks, leaks
```

That is the shape of what [adrgraph](https://github.com/crunchdomo/adrgraph) does with a whole
directory of decisions; this is the six-line version for one.
