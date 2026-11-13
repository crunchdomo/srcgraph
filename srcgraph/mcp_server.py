"""The graph for coding agents: the Repo's answers as MCP tools, so CLI, library and agent cannot disagree."""
from mcp.server import MCPServer

from srcgraph.api import Repo
from srcgraph.cli import PRESETS


def make_server(repo: Repo, allow_query: bool = False) -> MCPServer:
    server = MCPServer("srcgraph")
    for name, (help_, _, cypher) in PRESETS.items():
        if "$name" in cypher:
            server.add_tool(_named(repo, name), name=name, description=help_)
        elif "$tests" in cypher:
            server.add_tool(_with_tests(repo, name), name=name, description=help_)
        else:
            server.add_tool(_plain(repo, name), name=name, description=help_)

    def scope(glob: str, no_tests: bool = False) -> dict[str, list[dict]]:
        """One compartment by path glob: its files with doc lines, internal wiring, outbound and inbound wires."""
        return repo.scope(glob, no_tests)

    def impact(rev: str = "HEAD", hops: int = 2) -> dict[str, list[dict]]:
        """What a change reaches: the symbols a git revision or range (HEAD for the working tree, origin/main...HEAD
        for a branch) touches, what reaches them within hops, and the tests that cover them."""
        return repo.impact(rev, hops)

    def query(cypher: str) -> list[dict]:
        """Run Cypher against the graph. Tables: Unit, Symbol and Ast nodes; IMPORTS, CALLS, INHERITS and the other
        edges the README lists. Offered only when the server was started with --allow-query."""
        return repo.query(cypher)

    server.add_tool(scope)
    server.add_tool(impact)
    if allow_query:
        server.add_tool(query)
    return server


def _named(repo: Repo, preset: str):
    def tool(name: str) -> list[dict]:
        return getattr(repo, preset)(name)
    return tool


def _plain(repo: Repo, preset: str):
    def tool() -> list[dict]:
        return getattr(repo, preset)()
    return tool


def _with_tests(repo: Repo, preset: str):
    def tool(no_tests: bool = False) -> list[dict]:
        return getattr(repo, preset)(no_tests)
    return tool
