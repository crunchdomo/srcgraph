"""Shared graph vocabulary. Every extractor emits only these types."""
from dataclasses import dataclass, field

SYMBOL_KINDS = {"function", "method", "class", "variable", "resource", "module_call", "builtin", "unresolved"}
EDGE_LABELS = {"CONTAINS", "IMPORTS", "CALLS", "REFERENCES", "INHERITS", "DECORATES"}

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
    doc: str = ""   # first paragraph of the docstring, or ""
    test: bool = False   # lives under a tests directory or is named test_*.py / *_test.py


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
    doc: str = ""
    entry: bool = False   # reached from a __main__ guard, a declared console script, or a test


@dataclass(frozen=True)
class Edge:
    label: str
    src: str
    dst: str
    line: int = 0
    guard: str = ""   # CALLS, IMPORTS, REFERENCES: nearest enclosing if/for/while/try as kind@line, "typing" under
                      # `if TYPE_CHECKING`, "" when unconditional


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
    guard: str = ""   # as on Edge; "typing" when the import sits under `if TYPE_CHECKING`


REF_RELATIONS = {"calls": "CALLS", "references": "REFERENCES", "names": "REFERENCES", "reads": "REFERENCES",
                 "inherits": "INHERITS", "decorates": "DECORATES"}
# "names" is a bare identifier used as a value or annotation; the scanner keeps it only when it resolves to a scanned symbol.
# "reads" is the first argument of getattr(X, ...): X is a registry read by name; kept only when it is a scanned module.


@dataclass(frozen=True)
class RawRef:
    """A dotted name referenced from `src_id` (a Symbol id, or the Unit id at module scope).
    `relation` is a REF_RELATIONS key; the scanner resolves the name and emits the matching edge."""
    src_id: str
    name: str
    line: int
    relation: str
    entry: bool = False   # the call site sits under `if __name__ == "__main__"`
    guard: str = ""   # nearest enclosing if/for/while/try as kind@line, "" when unconditional


@dataclass
class Extraction:
    units: list[Unit] = field(default_factory=list)
    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    imports: list[RawImport] = field(default_factory=list)
    refs: list[RawRef] = field(default_factory=list)
    ast: list[AstNode] = field(default_factory=list)
    ast_edges: list[Edge] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def is_test_path(path: str) -> bool:
    parts = path.split("/")
    return any(p in ("test", "tests") for p in parts[:-1]) or parts[-1].startswith("test_") or parts[-1].endswith("_test.py")


def symbol_id(path: str, qualified: str) -> str:
    return f"{path}::{qualified}"


def unresolved_id(name: str) -> str:
    return f"unresolved::{name}"
