"""Walk source roots, run extractors, and resolve raw imports and references into graph edges."""
import builtins
import os
import posixpath
import re
import subprocess
import sys
import tomllib
from dataclasses import replace
from srcgraph.extractors import EXTRACTORS
from srcgraph.extractors.lua import BUILTINS as LUA_BUILTINS
from srcgraph.model import REF_RELATIONS, Edge, Extraction, RawImport, Symbol, Unit, symbol_id, unresolved_id
from srcgraph.selectors import glob_to_regex

BUILTIN_NAMES = frozenset(dir(builtins))
DEFAULT_EXCLUDES = (".*", "venv", "node_modules", "__pycache__")   # .* covers .git, .venv, .claude, .srcgraph, ...


def module_name(root: str, rel_to_root: str) -> str:
    for ext, init in ((".py", ".__init__"), (".lua", ".init")):   # a package's own file names the package
        if rel_to_root.endswith(ext):
            return rel_to_root[:-len(ext)].replace("/", ".").removesuffix(init)
    return rel_to_root


def _rel(path: str) -> str:
    """Path relative to the current directory, POSIX separators."""
    return posixpath.normpath(os.path.relpath(path).replace(os.sep, "/"))


def _walk(roots: list[str], excludes: tuple[str, ...]) -> list[tuple[str, str]]:
    """Yield (root, rel_path_from_cwd) for every file with a registered extractor."""
    # an exclude without "/" matches one path component at any depth; with "/" it matches the path from the root
    names = [re.compile(glob_to_regex(e)) for e in excludes if "/" not in e]
    paths = [re.compile(glob_to_regex(e.rstrip("/"))) for e in excludes if "/" in e]

    def excluded(rel: str) -> bool:
        parts = rel.split("/")
        if any(g.fullmatch(p) for p in parts if p not in (".", "..") for g in names):
            return True
        return any(g.fullmatch("/".join(parts[:i])) for i in range(1, len(parts) + 1) for g in paths)

    def walk(root: str):
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames
                                 if not os.path.islink(os.path.join(dirpath, d)) and not excluded(_rel(os.path.join(dirpath, d))))
            yield from (os.path.join(dirpath, fn) for fn in sorted(filenames))

    found = []
    for root in roots:
        tracked = _git_files(root)   # inside a git repo, git decides what is part of the project: .gitignore is honoured
        for full in (os.path.join(root, n) for n in tracked) if tracked is not None else walk(root):
            if os.path.splitext(full)[1] in EXTRACTORS and os.path.isfile(full) and not os.path.islink(full) and not excluded(_rel(full)):
                found.append((root, _rel(full)))
    # a file under two roots (--src src --src .) belongs to the most specific one, and is scanned once
    best: dict[str, str] = {}
    for root, rel in found:
        if rel not in best or len(root) > len(best[rel]):
            best[rel] = root
    return sorted((root, rel) for rel, root in best.items())


def _git_files(root: str) -> list[str] | None:
    """Tracked plus untracked-but-not-ignored files under root, sorted, relative to root; None outside a git repo."""
    try:
        out = subprocess.run(["git", "-C", root, "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                             capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    # nothing listed means the root itself is ignored (a vendored package, a venv): the user pointed at it, so walk it
    return sorted(os.fsdecode(p) for p in out.split(b"\0") if p) or None


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
    for root in roots:
        if os.path.isfile(os.path.join(root, "__init__.py")):
            res.warnings.append(f"'{root}' is itself a package: scan the directory that contains it, or its modules lose "
                                f"the package prefix and their absolute self-imports go unresolved")
    files = _walk(roots, excludes)
    if not files:
        res.warnings.append(f"no source files found under {', '.join(roots)}")
    for root, rel in files:
        rel_to_root = posixpath.relpath(rel, _rel(root))
        with open(rel, "rb") as fh:
            source = fh.read()
        ex = EXTRACTORS[os.path.splitext(rel)[1]](rel, source, module_name(root, rel_to_root))
        extractions.append(ex)
        for u in ex.units:
            if u.lang != "lua":   # a Lua module is found by lua_unit: tools/rec.lua is not Python's tools.rec
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
    # Python and Lua names are global (module paths); other languages resolve within a directory.
    def scope(lang: str, path: str) -> str:
        return "" if lang in ("python", "lua") else posixpath.dirname(path)

    by_qualified = {(s.lang, scope(s.lang, s.path), s.qualified): s.id for s in symbols.values()}

    def unit_for(name: str) -> str | None:
        return index[name][0] if name in index and name not in ambiguous else None

    lua_names = sorted((u.name, u.id) for u in units.values() if u.lang == "lua")

    def lua_unit(name: str) -> str | None:
        """The scanned Lua module a require names: by its own name, else the only one whose name ends with it
        (package.path is set at runtime, so the package root is often not a source root)."""
        exact = [uid for n, uid in lua_names if n == name]
        found = exact or [uid for n, uid in lua_names if n.endswith("." + name)]
        return found[0] if len(found) == 1 else None

    def external(name: str, lang: str) -> str:
        if name not in units:
            units[name] = Unit(name, name, name, lang, "external")
        return name

    def unresolved(name: str, lang: str) -> str:
        uid = unresolved_id(name)
        if uid not in symbols:
            symbols[uid] = Symbol(uid, name, name, "unresolved", lang, "", 0, 0, "")
        return uid

    def builtin(name: str, lang: str) -> str:
        """One shared Symbol per builtin, e.g. builtins::builtins.print, instead of an unresolved name per module.
        Another language's standard library has its own, builtins::lua.print."""
        qualified = f"{'builtins' if lang == 'python' else lang}.{name}"
        sid = symbol_id("builtins", qualified)
        if sid not in symbols:
            symbols[sid] = Symbol(sid, name, qualified, "builtin", lang, "builtins", 0, 0, "")
        return sid

    aliases: dict[str, dict[str, str]] = {}
    imports: set[tuple[str, str, int, str]] = set()   # (unit, target, line, guard): `from m import a, b` is one wire, not two
    typing_names: dict[str, set[str]] = {}   # unit -> names bound only under `if TYPE_CHECKING`
    for ex in extractions:
        for imp in ex.imports:
            importer = units[imp.unit_id]
            amap = aliases.setdefault(imp.unit_id, {})
            if importer.lang == "lua":
                target = lua_unit(imp.module)
                base = units[target].name if target else imp.module
                imports.add((imp.unit_id, target or external(imp.module, "lua"), imp.line, imp.guard))
                for name, alias in imp.names:
                    amap[alias] = f"{base}.{name}"
                if imp.alias:
                    amap[imp.alias] = base
                continue
            module = _absolute(imp, importer)
            if imp.names:
                for name, alias in imp.names:
                    dotted = f"{module}.{name}" if module else name
                    target = unit_for(dotted) or unit_for(module)
                    if target is None:
                        target = external(module or dotted, importer.lang)
                        _hint(res, index, module or dotted)
                    imports.add((imp.unit_id, target, imp.line, imp.guard))
                    amap[alias] = dotted
                    if imp.guard == "typing":
                        typing_names.setdefault(imp.unit_id, set()).add(alias)
            else:
                target = unit_for(module)
                if target is None:
                    target = external(module, importer.lang)
                    _hint(res, index, module)
                imports.add((imp.unit_id, target, imp.line, imp.guard))
                amap[imp.alias or module.split(".")[0]] = module if imp.alias else module.split(".")[0]
                if imp.guard == "typing":
                    typing_names.setdefault(imp.unit_id, set()).add(imp.alias or module.split(".")[0])

    res.edges.extend(Edge("IMPORTS", s, d, line=l, guard=g) for s, d, l, g in sorted(imports))

    # package -> child CONTAINS by name prefix
    for u in list(units.values()):
        if u.kind == "package":
            for other in units.values():
                if other.kind in ("module", "package") and other.name.rpartition(".")[0] == u.name:
                    res.edges.append(Edge("CONTAINS", u.id, other.id))

    def resolve(qualified: str, hops: int = 3) -> str | None:
        """A Python symbol by qualified name, following `from .m import name` in the package the name is read from."""
        sid = by_qualified.get(("python", "", qualified))
        if sid is not None or not hops:
            return sid
        parts = qualified.split(".")
        for i in range(len(parts) - 1, 0, -1):   # longest scanned unit prefix first
            uid = unit_for(".".join(parts[:i]))
            if uid is not None:
                alias = aliases.get(uid, {}).get(parts[i])
                # ponytail: three hops cover a facade over a facade; an __all__ built at runtime stays invisible
                return resolve(".".join([alias, *parts[i + 1:]]), hops - 1) if alias else None
        return None

    entry_ids: set[str] = set()
    for ex in extractions:
        for ref in ex.refs:
            src_unit = units[ref.src_id] if ref.src_id in units else units[symbols[ref.src_id].path]
            lang = src_unit.lang
            guard = ref.guard
            if lang == "python":
                head, dot, rest = ref.name.partition(".")
                amap = aliases.get(src_unit.id, {})
                if head in typing_names.get(src_unit.id, ()):   # an annotation naming a TYPE_CHECKING import is typing-only too
                    guard = guard or "typing"
                base = amap[head] if head in amap else f"{src_unit.name}.{head}"
                qualified = f"{base}{dot}{rest}"
                if ref.relation == "reads":   # getattr(module, name): the module is a registry; a wire to the unit, or nothing
                    uid = unit_for(qualified)
                    if uid is not None:
                        res.edges.append(Edge("REFERENCES", ref.src_id, uid, line=ref.line))
                    continue
                if ref.relation == "names":
                    sid = resolve(qualified)
                    if sid is None:   # not a scanned symbol: a local, a builtin or an unknown; no node for it
                        continue
                    if symbols[sid].kind == "variable" and symbols[sid].path == src_unit.path:
                        continue   # a module reading its own globals is not a wire worth drawing
                if head not in amap and head in BUILTIN_NAMES and (lang, "", f"{src_unit.name}.{head}") not in by_qualified:
                    target = builtin(ref.name, lang)
                    res.edges.append(Edge(REF_RELATIONS[ref.relation], ref.src_id, target, line=ref.line, guard=guard))
                    continue
            elif lang == "lua":
                head, dot, rest = ref.name.partition(".")
                amap = aliases.get(src_unit.id, {})
                qualified = f"{amap[head]}{dot}{rest}" if head in amap else f"{src_unit.name}.{ref.name}"
                target = by_qualified.get(("lua", "", qualified))
                if target is None and ref.relation == "names":
                    continue   # a name that is no scanned function: a value, a field, a constant
                if target is None and head not in amap:   # not this file's and not a required module's: a global
                    target = builtin(ref.name, lang) if head in LUA_BUILTINS else unresolved(ref.name, lang)
                target = target or unresolved(qualified, lang)
                res.edges.append(Edge(REF_RELATIONS[ref.relation], ref.src_id, target, line=ref.line, guard=guard))
                if ref.relation == "calls" and src_unit.test:
                    entry_ids.add(target)
                continue
            else:
                qualified = ref.name
            target = (resolve(qualified) if lang == "python" else by_qualified.get((lang, scope(lang, src_unit.path), qualified))
                      ) or unresolved(qualified, lang)
            res.edges.append(Edge(REF_RELATIONS[ref.relation], ref.src_id, target, line=ref.line, guard=guard))
            if ref.relation == "calls" and (ref.entry or src_unit.test):
                entry_ids.add(target)
    for root in roots:  # console scripts declared in pyproject.toml: "pkg.mod:func"
        try:
            with open(os.path.join(root, "pyproject.toml"), "rb") as fh:
                scripts = tomllib.load(fh).get("project", {}).get("scripts", {})
        except (OSError, tomllib.TOMLDecodeError):
            scripts = {}
        for target in scripts.values():
            mod, _, func = target.partition(":")
            sid = by_qualified.get(("python", "", f"{mod}.{func}" if func else mod))
            if sid:
                entry_ids.add(sid)

    res.units = list(units.values())
    res.symbols = [replace(s, entry=True) if s.id in entry_ids and s.kind not in ("builtin", "unresolved") else s
                   for s in symbols.values()]
    return res
