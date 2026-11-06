"""Shared graph vocabulary. Every extractor emits only these types."""
from dataclasses import dataclass, field

UNIT_KINDS = {"file", "package", "module", "external"}
SYMBOL_KINDS = {"function", "method", "class", "type", "variable", "resource", "module_call", "unresolved"}
EDGE_LABELS = {"CONTAINS", "IMPORTS", "CALLS", "REFERENCES", "INHERITS", "DECORATES", "READS", "WRITES", "TYPE_OF", "OVERRIDES"}
AST_EDGE_LABELS = {"CHILD", "NEXT"}
LINK_EDGE_LABEL = "DERIVED_FROM"

# Normalised syntax kinds: one vocabulary across grammars. Owned here; extractors only map onto it.
NORM_KINDS = ("ClassDef", "FunctionDef", "Call", "Assign", "Import", "Decorator", "Attribute", "Name", "String",
              "Return", "If", "Loop", "Try", "Lambda", "Parameter", "Argument", "Block", "Comment", "Type", "Literal")
# Syntax nodes whose source text is stored even though they are not leaves.
TEXT_NORMS = {"Decorator", "Attribute", "String", "Name", "Literal", "Type", "Import"}
TEXT_LIMIT = 512


@dataclass(frozen=True)
class Unit:
    id: str
    path: str
    name: str
    lang: str
    kind: str


@dataclass(frozen=True)
class Symbol:
    id: str
    name: str
    qualified: str
    kind: str
    lang: str
    path: str
    line: int
    end_line: int
    ast_kind: str


@dataclass(frozen=True)
class Edge:
    label: str
    src: str
    dst: str
    line: int = 0


@dataclass(frozen=True)
class AstNode:
    """One named node of a file's syntax tree, exactly as the parser produced it."""
    id: str
    path: str
    kind: str       # the grammar's node type, e.g. class_definition
    norm: str       # a NORM_KINDS entry or "" when the grammar type has no mapping
    lang: str
    line: int
    end_line: int
    start_byte: int
    end_byte: int
    text: str       # stored for leaves and TEXT_NORMS, else ""
    field: str      # the field name this node occupies in its parent, or ""


@dataclass(frozen=True)
class RawImport:
    """An import as written. `names` are (imported_name, local_alias) pairs for
    `from X import a as b`; `alias` is the local name for `import X as alias`;
    `level` is the number of leading dots."""
    unit_id: str
    module: str
    level: int
    line: int
    names: tuple[tuple[str, str], ...] = ()
    alias: str | None = None


REF_RELATIONS = {"calls": "CALLS", "references": "REFERENCES", "inherits": "INHERITS", "decorates": "DECORATES"}


@dataclass(frozen=True)
class RawRef:
    """A dotted name referenced from `src_id` (a Symbol id, or the Unit id at module scope).
    `relation` is a REF_RELATIONS key; the scanner resolves the name and emits the matching edge."""
    src_id: str
    name: str
    line: int
    relation: str


@dataclass
class Extraction:
    units: list[Unit] = field(default_factory=list)
    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    imports: list[RawImport] = field(default_factory=list)
    refs: list[RawRef] = field(default_factory=list)
    ast: list[AstNode] = field(default_factory=list)
    ast_edges: list[Edge] = field(default_factory=list)


def symbol_id(path: str, qualified: str) -> str:
    return f"{path}::{qualified}"


def unresolved_id(name: str) -> str:
    return f"unresolved::{name}"
