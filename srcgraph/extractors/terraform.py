"""Terraform (HCL) extractor: top-level blocks become symbols, references become REFERENCES edges."""
import tree_sitter_hcl
from tree_sitter import Language, Node, Parser

from srcgraph.extractors.lines import LineIndex
from srcgraph.extractors.syntax import ast_id, emit_syntax
from srcgraph.model import Edge, Extraction, RawRef, Symbol, Unit, symbol_id

LANG = "terraform"
_PARSER = Parser(Language(tree_sitter_hcl.language()))
# Blocks stay unnormalised: they are already first-class Symbols.
TF_NORM = {"attribute": "Assign", "identifier": "Name", "string_lit": "String", "function_call": "Call",
           "numeric_lit": "Literal", "bool_lit": "Literal", "variable_expr": "Name", "get_attr": "Attribute"}
_COMPONENTS = {"var": 2, "local": 2, "module": 2, "data": 3}


def _text(node: Node) -> str:
    return node.text.decode("utf-8", "replace")


def _label(node: Node) -> str:
    """The inner text of a string_lit label like "aws_s3_bucket"."""
    return "".join(_text(c) for c in node.children if c.type == "template_literal")


def _block_name(block: Node) -> tuple[str, str] | None:
    kind = None
    labels = []
    for c in block.children:
        if c.type == "identifier" and kind is None:
            kind = _text(c)
        elif c.type == "string_lit":
            labels.append(_label(c))
    if kind == "resource" and len(labels) == 2:
        return f"{labels[0]}.{labels[1]}", "resource"
    if kind == "data" and len(labels) == 2:
        return f"data.{labels[0]}.{labels[1]}", "resource"
    if kind == "variable" and labels:
        return f"var.{labels[0]}", "variable"
    if kind == "output" and labels:
        return f"output.{labels[0]}", "variable"
    if kind == "module" and labels:
        return f"module.{labels[0]}", "module_call"
    return None


def _references(node: Node) -> set[str]:
    """Every referenced symbol name under node, truncated to the symbol prefix."""
    out = set()
    if node.type == "variable_expr":
        parts = [_text(node)]
        sib = node.next_sibling
        while sib is not None and sib.type == "get_attr":
            parts.append(_text(sib).lstrip("."))
            sib = sib.next_sibling
        n = _COMPONENTS.get(parts[0], 2)
        if len(parts) >= n:
            out.add(".".join(parts[:n]))
    for c in node.children:
        out |= _references(c)
    return out


def extract(rel_path: str, source: bytes, module_name: str) -> Extraction:
    ex = Extraction()
    ex.units.append(Unit(rel_path, rel_path, rel_path, LANG, "file"))
    tree = _PARSER.parse(source)
    lines = LineIndex(source)
    ex.ast, ex.ast_edges = emit_syntax(tree.root_node, TF_NORM, rel_path, LANG, lines)
    ex.edges.append(Edge("DERIVED_FROM", rel_path, ast_id(rel_path, tree.root_node)))
    body = next((c for c in tree.root_node.children if c.type == "body"), None)
    if body is None:
        return ex

    def add(node: Node, name: str, kind: str) -> None:
        sym = Symbol(symbol_id(rel_path, name), name, name, kind, LANG, rel_path,
                     lines.line(node.start_byte), lines.line(max(node.end_byte - 1, node.start_byte)), node.type)
        ex.symbols.append(sym)
        ex.edges.append(Edge("CONTAINS", rel_path, sym.id))
        ex.edges.append(Edge("DERIVED_FROM", sym.id, ast_id(rel_path, node)))
        for ref in sorted(_references(node)):
            ex.refs.append(RawRef(sym.id, ref, lines.line(node.start_byte), "references"))

    for block in body.children:
        if block.type != "block":
            continue
        first = next((c for c in block.children if c.type == "identifier"), None)
        if first is not None and _text(first) == "locals":
            inner = next((c for c in block.children if c.type == "body"), None)
            for attr in (inner.children if inner else []):
                if attr.type == "attribute":
                    add(attr, f"local.{_text(attr.children[0])}", "variable")
            continue
        named = _block_name(block)
        if named:
            add(block, *named)
    return ex
