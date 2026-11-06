"""Syntax tier: every named tree-sitter node as an AstNode, with CHILD and NEXT edges.
Shared by all extractors; the only per-language input is the grammar-kind -> NORM_KINDS mapping."""
from tree_sitter import Node

from srcgraph.extractors.lines import LineIndex
from srcgraph.model import TEXT_LIMIT, TEXT_NORMS, AstNode, Edge


def ast_id(path: str, node: Node) -> str:
    return f"{path}#{node.start_byte}:{node.end_byte}:{node.type}"


def emit_syntax(root: Node, mapping: dict[str, str], path: str, lang: str, lines: LineIndex
                ) -> tuple[list[AstNode], list[Edge]]:
    nodes: dict[str, AstNode] = {}
    edges: list[Edge] = []
    stack = [(root, "", None)]  # (node, field in parent, parent id)
    while stack:
        node, field, parent = stack.pop()
        nid = ast_id(path, node)
        named = [(i, c) for i, c in enumerate(node.children) if c.is_named]
        if nid not in nodes:
            norm = mapping.get(node.type, "")
            text = ""
            if not named or norm in TEXT_NORMS:
                text = node.text[:TEXT_LIMIT].decode("utf-8", "replace")
            nodes[nid] = AstNode(nid, path, node.type, norm, lang, lines.line(node.start_byte),
                                 lines.line(max(node.end_byte - 1, node.start_byte)),
                                 node.start_byte, node.end_byte, text, field)
            if parent is not None:
                edges.append(Edge("CHILD", parent, nid))
            prev = None
            for i, child in named:
                cid = ast_id(path, child)
                if prev is not None:
                    edges.append(Edge("NEXT", prev, cid))
                prev = cid
            for i, child in reversed(named):  # reversed so the stack pops in source order
                stack.append((child, node.field_name_for_child(i) or "", nid))
    return list(nodes.values()), edges
