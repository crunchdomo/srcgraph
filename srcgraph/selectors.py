"""Selectors: small dicts that pick graph nodes. Compiled to Cypher boolean fragments."""
import re

from srcgraph.model import SYMBOL_KINDS

KEYS = ("path", "kind", "name", "lang")


class SelectorError(ValueError):
    pass


def glob_to_regex(glob: str) -> str:
    out, i = [], 0
    if glob.startswith("**/"):
        out.append("(?:.*/)?")
        i = 3
    while i < len(glob):
        c = glob[i]
        if glob.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return "^" + "".join(out) + "$"


def compile_selector(sel: dict, var: str, prefix: str) -> tuple[str, dict]:
    for key in sel:
        if key not in KEYS:
            raise SelectorError(f"unknown selector key '{key}' (allowed: {', '.join(KEYS)})")
    parts, params = [], {}
    is_ast = sel.get("kind") == "ast"
    if "kind" not in sel:
        parts.append(f"label({var}) <> 'Ast'")  # the syntax tier is reached only on request
    if "path" in sel:
        params[f"{prefix}_path"] = glob_to_regex(sel["path"])
        parts.append(f"{var}.path =~ ${prefix}_path")
    if "kind" in sel:
        kind = sel["kind"]
        if kind == "unit":
            parts.append(f"label({var}) = 'Unit'")
        elif kind == "ast":
            parts.append(f"label({var}) = 'Ast'")
        elif kind in SYMBOL_KINDS:
            params[f"{prefix}_kind"] = kind
            parts.append(f"label({var}) = 'Symbol' AND {var}.kind = ${prefix}_kind")
        else:
            raise SelectorError(f"unknown kind '{kind}' (allowed: unit, ast, {', '.join(sorted(SYMBOL_KINDS))})")
    if "name" in sel:
        p = f"${prefix}_name"
        params[f"{prefix}_name"] = glob_to_regex(sel["name"])
        if is_ast:
            parts.append(f"{var}.text =~ {p}")
        else:
            parts.append(f"(label({var}) = 'Unit' AND {var}.name =~ {p}) OR "
                         f"(label({var}) = 'Symbol' AND ({var}.name =~ {p} OR {var}.qualified =~ {p}))")
    if "lang" in sel:
        params[f"{prefix}_lang"] = sel["lang"]
        parts.append(f"{var}.lang = ${prefix}_lang")
    if not parts:
        return "true", {}
    return " AND ".join(f"({p})" for p in parts), params
