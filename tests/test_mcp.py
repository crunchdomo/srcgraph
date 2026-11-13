"""The MCP server as an agent meets it: a real MCP client connected in-process to the server over the fixture repo."""
import asyncio
import os

import pytest

pytest.importorskip("mcp")   # the optional extra: pip install 'srcgraph[mcp]'
from mcp import Client   # noqa: E402

import srcgraph   # noqa: E402
from srcgraph.mcp_server import make_server   # noqa: E402

ROOT = os.path.abspath("tests/fixtures/pyrepo")


@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    db = str(tmp_path_factory.mktemp("mcp") / "g.lbug")
    with srcgraph.load(ROOT, db=db) as g:
        yield g


def ask(repo, fn, **options):
    """Run one exchange with a fresh client and return what the agent got."""
    async def go():
        async with Client(make_server(repo, **options)) as client:
            return await fn(client)
    return asyncio.run(go())


def rows(repo, tool, args=None, **options):
    result = ask(repo, lambda c: c.call_tool(tool, args or {}), **options)
    assert not result.is_error, result.content
    return result.structured_content["result"]


def test_an_agent_asks_who_calls_a_symbol(repo):
    assert rows(repo, "callers", {"name": "call"})[0]["caller"] == "src.handlers.h.view"


def test_an_agent_asks_who_imports_a_module(repo):
    assert rows(repo, "importers", {"name": "logging"})[0]["importer"] == "src/handlers/h.py"


def test_an_agent_finds_a_symbol_by_a_word_in_any_case(repo):
    hit = rows(repo, "find", {"name": "WARM"})[0]
    assert (hit["node"], hit["path"], hit["line"]) == ("src.contracts.c.warm", "src/contracts/c.py", 10)


def test_an_agent_asks_what_is_dead(repo):
    assert "src.handlers.h.leak" in {r["symbol"] for r in rows(repo, "dead")}


def test_an_agent_ranks_hubs_with_or_without_the_tests(repo):
    fan_in = lambda rs: {r["node"]: r["fan_in"] for r in rs}["src/contracts/c.py"]   # noqa: E731
    assert fan_in(rows(repo, "hubs")) == 6
    assert fan_in(rows(repo, "hubs", {"no_tests": True})) == 5   # the test file no longer counts as a neighbour


def answer(repo, tool, args=None, **options):
    result = ask(repo, lambda c: c.call_tool(tool, args or {}), **options)
    assert not result.is_error, result.content
    return result.structured_content


def test_an_agent_looks_at_one_compartment(repo):
    sections = answer(repo, "scope", {"glob": "src/contracts/**"})
    assert set(sections) == {"doc", "internal", "outbound", "inbound"} and sections["inbound"]


def test_an_agent_asks_what_a_change_reaches(tmp_path):
    path = os.path.join(ROOT, "src/contracts/c.py")
    original = open(path).read()
    try:
        open(path, "w").write(original.replace("    return Contract().send()", "    req = req\n    return Contract().send()"))
        with srcgraph.load(ROOT, db=str(tmp_path / "g.lbug")) as changed_repo:
            impact = answer(changed_repo, "impact", {"rev": "HEAD"})
    finally:
        open(path, "w").write(original)
    assert [r["symbol"] for r in impact["changed"]] == ["src.contracts.c.call"]
    assert "src.handlers.h.view" in {r["symbol"] for r in impact["reached"]}   # one hop: view calls call
    assert "tests.uses_call.test_call" in {r["test"] for r in impact["tests"]}


NAVIGATION = {"find", "callers", "importers", "hubs", "dead", "untested", "scope", "impact"}


def test_raw_cypher_is_offered_only_when_the_server_allows_it(repo):
    names = lambda **options: {t.name for t in ask(repo, lambda c: c.list_tools(), **options).tools}   # noqa: E731
    assert names() == NAVIGATION
    assert names(allow_query=True) == NAVIGATION | {"query"}
    cypher = "MATCH (u:Unit {test: true}) RETURN u.path AS p"
    assert rows(repo, "query", {"cypher": cypher}, allow_query=True) == [{"p": "tests/uses_call.py"}]
    refused = ask(repo, lambda c: c.call_tool("query", {"cypher": cypher}))
    assert refused.is_error


def test_srcgraph_mcp_builds_then_speaks_mcp_over_stdio(tmp_path):
    import sys
    from mcp import StdioServerParameters
    checkout = os.path.abspath(".")
    server = StdioServerParameters(
        command=sys.executable, args=["-m", "srcgraph.cli", "mcp", "--db", str(tmp_path / "g.lbug")], cwd=ROOT,
        env={"PATH": os.environ["PATH"], "PYTHONPATH": checkout})

    async def go():
        async with Client(server) as client:   # no database yet: the build's own output must stay off stdout
            names = {t.name for t in (await client.list_tools()).tools}
            called = await client.call_tool("callers", {"name": "call"})
            return names, called.structured_content["result"]

    names, callers = asyncio.run(go())
    assert names == NAVIGATION
    assert callers[0]["caller"] == "src.handlers.h.view"


def test_without_the_sdk_the_command_says_which_extra_to_install(monkeypatch, capsys):
    import sys
    from srcgraph.cli import main
    for module in ("mcp", "mcp.server"):   # as if the optional extra were not installed
        monkeypatch.setitem(sys.modules, module, None)
    monkeypatch.delitem(sys.modules, "srcgraph.mcp_server", raising=False)
    assert main(["mcp"]) == 2
    assert "pip install 'srcgraph[mcp]'" in capsys.readouterr().err
