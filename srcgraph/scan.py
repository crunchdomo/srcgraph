"""Walk source roots, run extractors, and resolve raw imports and references into graph edges."""
import os
import posixpath
import re
import sys
from srcgraph.extractors import EXTRACTORS
from srcgraph.model import REF_RELATIONS, Edge, Extraction, RawImport, Symbol, Unit, unresolved_id
from srcgraph.selectors import glob_to_regex

DEFAULT_EXCLUDES = (".git", ".venv", "venv", "node_modules", ".terraform", ".adrgraph", ".srcgraph", "__pycache__")


def module_name(root: str, rel_to_root: str) -> str:
    name = rel_to_root[:-3].replace("/", ".") if rel_to_root.endswith(".py") else rel_to_root
    return name.removesuffix(".__init__")


def _rel(path: str) -> str:
    """Path relative to the current directory, POSIX separators."""
    return posixpath.normpath(os.path.relpath(path).replace(os.sep, "/"))


def _walk(roots: list[str], excludes: tuple[str, ...]) -> list[tuple[str, str]]:
    """Yield (root, rel_path_from_cwd) for every file with a registered extractor."""
    names = {e for e in excludes if "/" not in e and not any(c in e for c in "*?")}
    globs = [re.compile(glob_to_regex(e.rstrip("/"))) for e in excludes if e not in names]

    def excluded(rel: str) -> bool:
        parts = rel.split("/")
        if any(p in names for p in parts):
            return True
        return any(g.fullmatch("/".join(parts[:i])) for i in range(1, len(parts) + 1) for g in globs)

    found = []
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames
                                 if not os.path.islink(os.path.join(dirpath, d)) and not excluded(_rel(os.path.join(dirpath, d))))
            for fn in sorted(filenames):
                full = os.path.join(dirpath, fn)
                if os.path.islink(full) or os.path.splitext(fn)[1] not in EXTRACTORS:
                    continue
                rel = _rel(full)
                if not excluded(rel):
                    found.append((root, rel))
    return found


def _absolute(imp: RawImport, importer: Unit) -> str:
    if imp.level == 0:
        return imp.module
    base = importer.name if importer.kind == "package" else importer.name.rpartition(".")[0]
    for _ in range(imp.level - 1):
        base = base.rpartition(".")[0]
    return f"{base}.{imp.module}" if imp.module and base else (imp.module or base)


def _hint(res: Extraction, index: dict[str, list[str]], name: str) -> None:
    """Warn when an import went external but a scanned module has that name under a deeper prefix (src layout)."""
    if name.split(".")[0] in sys.stdlib_module_names:
        return  # a scanned module shadowing a stdlib name is not a misconfigured root
    for key in index:
        if key.endswith("." + name):
            root = key[: -len(name) - 1].replace(".", "/")
            msg = (f"import '{name}' resolved as external but matches scanned module '{key}'; "
                   f"if '{root}/' is a source root, pass --src {root}")
            if msg not in res.warnings:
                res.warnings.append(msg)
            return


def scan(roots: list[str], excludes: tuple[str, ...] = DEFAULT_EXCLUDES) -> Extraction:
    res = Extraction()
    extractions: list[Extraction] = []
    index: dict[str, list[str]] = {}
    for root, rel in _walk(roots, excludes):
        rel_to_root = posixpath.relpath(rel, _rel(root))
        with open(rel, "rb") as fh:
            source = fh.read()
        ex = EXTRACTORS[os.path.splitext(rel)[1]](rel, source, module_name(root, rel_to_root))
        extractions.append(ex)
        for u in ex.units:
            index.setdefault(u.name, []).append(u.id)

    ambiguous = {name for name, ids in index.items() if len(ids) > 1}
    for name in sorted(ambiguous):
        res.warnings.append(f"ambiguous module {name}: {', '.join(index[name])}")

    units: dict[str, Unit] = {}
    symbols: dict[str, Symbol] = {}
    for ex in extractions:
        units.update((u.id, u) for u in ex.units)
        symbols.update((s.id, s) for s in ex.symbols)
        res.edges.extend(ex.edges)
        res.ast.extend(ex.ast)
        res.ast_edges.extend(ex.ast_edges)
    # Python names are global (module paths); other languages resolve within a directory.
    def scope(lang: str, path: str) -> str:
        return "" if lang == "python" else posixpath.dirname(path)

    by_qualified = {(s.lang, scope(s.lang, s.path), s.qualified): s.id for s in symbols.values()}

    def unit_for(name: str) -> str | None:
        return index[name][0] if name in index and name not in ambiguous else None

    def external(name: str, lang: str) -> str:
        if name not in units:
            units[name] = Unit(name, name, name, lang, "external")
        return name

    def unresolved(name: str, lang: str) -> str:
        uid = unresolved_id(name)
        if uid not in symbols:
            symbols[uid] = Symbol(uid, name, name, "unresolved", lang, "", 0, 0, "")
        return uid

    aliases: dict[str, dict[str, str]] = {}
    for ex in extractions:
        for imp in ex.imports:
            importer = units[imp.unit_id]
            amap = aliases.setdefault(imp.unit_id, {})
            module = _absolute(imp, importer)
            if imp.names:
                for name, alias in imp.names:
                    dotted = f"{module}.{name}" if module else name
                    target = unit_for(dotted) or unit_for(module)
                    if target is None:
                        target = external(module or dotted, importer.lang)
                        _hint(res, index, module or dotted)
                    res.edges.append(Edge("IMPORTS", imp.unit_id, target, line=imp.line))
                    amap[alias] = dotted
            else:
                target = unit_for(module)
                if target is None:
                    target = external(module, importer.lang)
                    _hint(res, index, module)
                res.edges.append(Edge("IMPORTS", imp.unit_id, target, line=imp.line))
                amap[imp.alias or module.split(".")[0]] = module if imp.alias else module.split(".")[0]

    # package -> child CONTAINS by name prefix
    for u in list(units.values()):
        if u.kind == "package":
            for other in units.values():
                if other.kind in ("module", "package") and other.name.rpartition(".")[0] == u.name:
                    res.edges.append(Edge("CONTAINS", u.id, other.id))

    for ex in extractions:
        for ref in ex.refs:
            src_unit = units[ref.src_id] if ref.src_id in units else units[symbols[ref.src_id].path]
            lang = src_unit.lang
            if lang == "python":
                head, dot, rest = ref.name.partition(".")
                amap = aliases.get(src_unit.id, {})
                base = amap[head] if head in amap else f"{src_unit.name}.{head}"
                qualified = f"{base}{dot}{rest}"
            else:
                qualified = ref.name
            target = by_qualified.get((lang, scope(lang, src_unit.path), qualified)) or unresolved(qualified, lang)
            res.edges.append(Edge(REF_RELATIONS[ref.relation], ref.src_id, target, line=ref.line))

    res.units = list(units.values())
    res.symbols = list(symbols.values())
    return res
