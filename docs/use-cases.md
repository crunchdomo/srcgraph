# Use cases, on three public repos

Flask, Requests and FastAPI, cloned at their main branches on 2026-11-08 and scanned with the srcgraph
at this commit. Commands are as run; output is as printed, cut with `…` where a table was longer.
Every preset here prints candidates with an error bar, not verdicts: the last section says where
these answers were wrong and why.

| repo | build | units / symbols / syntax nodes | wall time |
|---|---|---|---|
| pallets/flask | `srcgraph build --src src --src tests` | 145 / 1582 / 80833 | 5 s |
| psf/requests | `srcgraph build --src src --src tests` | 105 / 1161 / 58055 | 4 s |
| fastapi/fastapi | `srcgraph build --exclude docs_src --exclude docs --exclude scripts` | 948 / 7253 / 667023 | 41 s |

## 1. First week on a codebase (Flask)

The two questions a newcomer has are "what does the most" and "what must I not break". `hubs`
answers both: fan-out is the first, fan-in the second.

```
$ srcgraph hubs
node                       fan_in  fan_out
src/flask/cli.py           6       41
src/flask/__init__.py      35      10
src/flask/app.py           6       38
src/flask/sansio/app.py    7       28
src/flask/globals.py       17      15
flask.app.Flask            24      5
src/flask/sansio/scaffold.py  5    23
flask.ctx.AppContext       25      0
…
```

`cli.py` and `app.py` do the most; `Flask`, `AppContext` and `globals.py` are what everything else
holds on to. The one directory with its own README is `src/flask/sansio/`, and the README states a
rule: the code there may do no IO and may not use the Flask globals, so that Quart can reuse it.
`domains` is how you check a rule like that against the code:

```
$ srcgraph domains sansio='src/flask/sansio/**' json='src/flask/json/**' cli='src/flask/cli.py' app='src/flask/app.py' blueprints='src/flask/blueprints.py'
from        to      edges
blueprints  sansio  5
sansio      json    5
app         cli     4
json        sansio  3
app         sansio  2
blueprints  cli     2
cli         app     1
== crossings (22)
from    to      source                                    label       target                               line
app     cli     flask.app.Flask.__init__                  CALLS       flask.cli.AppGroup                   338
app     sansio  flask.app.Flask                           INHERITS    flask.sansio.app.App                 108
json    sansio  flask.json.provider.JSONProvider.__init__ REFERENCES  flask.sansio.app.App                 38
sansio  json    flask.sansio.app.App                      REFERENCES  flask.json.provider.JSONProvider     228
sansio  json    src/flask/sansio/app.py                   IMPORTS     flask.json.provider                  27
…
```

Two things a newcomer would not get from reading files one at a time. `sansio` and `json` depend
on each other (5 edges one way, 3 the other), and so do `app` and `cli`. And nothing under `sansio`
reaches `flask.globals`: the README's rule holds, at one hop. `scope` gives the full outbound list
for the compartment, which is where the second hop would start:

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
flask.sansio.app.App          REFERENCES  flask.testing.FlaskClient              267
flask.sansio.app.App          REFERENCES  flask.json.provider.DefaultJSONProvider 228
src/flask/sansio/app.py       IMPORTS     flask.helpers                          25
src/flask/sansio/app.py       IMPORTS     flask.templating                       30
src/flask/sansio/app.py       IMPORTS     flask.testing                          40
…
```

The header shows the three files have no doc line: nothing at the top of any of them says what it
is for, which is a fair first pull request. The outbound list mixes a runtime import of
`flask.templating` (line 30) with a `from ..testing import FlaskClient` that sits under
`if TYPE_CHECKING` (line 40); for the sans-IO question that is the whole difference, and the graph
does not yet show it ([#58](https://github.com/crunchdomo/srcgraph/issues/58)).

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
$ srcgraph callers requests.sessions.Session.request
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
`Session.request` on its own, without the module path, finds nothing today
([#57](https://github.com/crunchdomo/srcgraph/issues/57)).

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

`adapters.py` has eleven of the lines; it is the urllib3 boundary. And the module matrix says
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

Everything leans on `models`. `hubs` agrees: `PreparedRequest` (fan-in 36), `Response` (33) and
`CaseInsensitiveDict` (32) are the three most depended-on symbols in the package.

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

Reading it: `openapi` depends on `routing` (17 edges, all `REFERENCES` to `APIRoute`,
`RouteContext` and `_APIRouteLike` from `openapi/utils.py`) and `routing` never depends back, so
OpenAPI generation is downstream of routing, as it should be. `security → openapi` is 23 edges
because every security class builds its own OpenAPI model in `__init__`
(`fastapi.security.http.HTTPBase.__init__ CALLS fastapi.openapi.models.HTTPBase`, line 80); that is
by design, and now it is a stated fact rather than something you discover in a diff review. The
`starlette` boundary is two files:

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

`dead` and `untested` are the tidy-up presets, and on these three they are where the graph's blind
spots show.

| repo | `dead` rows | `untested` rows |
|---|---|---|
| flask | 18 | 363 |
| requests | 27 | 244 |
| fastapi | 32 | 439 |

```
$ srcgraph dead          # flask
symbol                                  path                        line
flask.app._make_timedelta               src/flask/app.py            72
flask.cli._path_is_ancestor             src/flask/cli.py            691
flask.ctx.has_request_context           src/flask/ctx.py            209
flask.helpers.url_for                   src/flask/helpers.py        200
flask.helpers.flash                     src/flask/helpers.py        326
flask.templating.render_template_string src/flask/templating.py     151
…
```

The first two rows are real candidates: `_make_timedelta` in `app.py` duplicates the one in
`sansio/app.py`, and nothing calls `_path_is_ancestor`. The rest are Flask's public API, which the
tests call constantly as `from flask import url_for`. That import resolves to the `flask` package,
whose `__init__` re-exports `url_for` from `helpers`, and the graph does not follow the re-export
([#55](https://github.com/crunchdomo/srcgraph/issues/55)). On FastAPI the same gap puts `FastAPI`,
`Depends`, `Path` and `Header` in `dead`; on Requests it puts `requests.api.get` and `post` there.
Until #55 lands, read `dead` and `untested` on a library as "unused from inside the package, by
anything that imports it by its full path". On an application, where nothing re-exports, they are
what they say.

The empty answers carry their own error bar:

```
$ srcgraph callers Flask
no callers of Flask (773 of 2000 calls resolved; a method called on anything but self or a module leaves no edge, so it can look uncalled)
```

Two more things found while scanning. Requests keeps `tests/` as a package (`tests/__init__.py`,
`from tests.utils import …`), so `--src tests` drops the `tests.` prefix and the build says to scan
the parent instead; doing that with `--src src --src .` scans `src/` twice and the load fails on a
duplicate key ([#56](https://github.com/crunchdomo/srcgraph/issues/56)). `--src src --src tests` is
what the table above used, and its one cost is that the two `from tests…` imports inside the
tests go unresolved.

## 5. As a library: one rule in CI

Everything above is a dict away in Python. The sans-IO rule from section 1 as a check:

```python
import srcgraph

g = srcgraph.load(".", src=["src", "tests"])
crossings = g.domains({"sansio": "src/flask/sansio/**", "globals": "src/flask/globals.py"})["crossings"]
leaks = [c for c in crossings if c["from"] == "sansio"]
assert not leaks, leaks
```

That is the shape of what [adrgraph](https://github.com/crunchdomo/adrgraph) does with a whole
directory of decisions; this is the six-line version for one.
