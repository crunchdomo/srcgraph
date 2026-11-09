"""Model-written summaries for the units and symbols that have no doc line.

`run` asks Claude for one sentence per undocumented node and stores it as `summary`, never as `doc`. Summaries
live in a sidecar JSON next to the database, keyed by node id with a hash of the source they describe, so
`build` carries them over (`apply_cache`) and a later `describe` pays only for what changed."""
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from srcgraph.model import Extraction
from srcgraph.source import Source

MODEL = "claude-haiku-5-5"
KINDS = ("function", "method", "class")
UNIT_LINES = 60      # a module is summarised from its head
SOURCE_LIMIT = 6000  # characters sent per node
PROMPT = ("Write one sentence, under 25 words, saying what this {kind} is for. Plain prose, no preamble, "
          "no code formatting, do not repeat its name.")


def cache_path(db_path: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(db_path)), "summaries.json")


def _load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def source_of(source: Source, path: str, line: int, end_line: int) -> str | None:
    lines = source.lines(path)
    if lines is None:
        return None
    end = end_line if end_line else min(len(lines), UNIT_LINES)
    return "\n".join(lines[max(line, 1) - 1:end])[:SOURCE_LIMIT]


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def apply_cache(result: Extraction, source: Source, path: str) -> None:
    """Set `summary` on the extraction's nodes from the sidecar, where the source still hashes the same."""
    cache = _load(path)
    if not cache:
        return
    for attr, line_of in (("units", lambda u: (1, 0)), ("symbols", lambda s: (s.line, s.end_line))):
        nodes = getattr(result, attr)
        for i, node in enumerate(nodes):
            hit = cache.get(node.id)
            if hit and not node.doc:
                text = source_of(source, node.path, *line_of(node))
                if text is not None and _hash(text) == hit["hash"]:
                    nodes[i] = replace(node, summary=hit["summary"])


def _summarise_with_claude(model: str):
    try:
        import anthropic
    except ImportError:
        raise SystemExit("describe needs the anthropic package: pip install 'srcgraph[describe]'")
    client = anthropic.Anthropic()

    def summarise(kind: str, text: str) -> str:
        r = client.messages.create(model=model, max_tokens=200, output_config={"effort": "low"},
                                   system=PROMPT.format(kind=kind), messages=[{"role": "user", "content": text}])
        if r.stop_reason == "refusal":
            return ""
        return " ".join(b.text for b in r.content if b.type == "text").strip().splitlines()[0] if r.content else ""
    return summarise


def run(graph, source: Source, path: str, model: str = MODEL, out=None, err=None, summarise=None) -> int:
    """Summarise every undocumented node whose source is new or changed, update the sidecar and the open graph.
    `summarise(kind, text) -> str` defaults to Claude; tests pass their own."""
    cache = _load(path)
    todo = []
    for row in graph.query("MATCH (n:Symbol:Unit) WHERE n.doc = '' AND (label(n) = 'Unit' AND n.kind <> 'external' "
                           "OR n.kind IN $kinds) RETURN n.id AS id, n.kind AS kind, n.path AS path, "
                           "n.line AS line, coalesce(n.end_line, 0) AS end_line", {"kinds": list(KINDS)}):
        text = source_of(source, row["path"], row["line"], row["end_line"])
        if text is None or not text.strip():
            continue
        h = _hash(text)
        hit = cache.get(row["id"])
        if hit and hit["hash"] == h:
            if hit["summary"]:
                graph.query("MATCH (n) WHERE n.id = $id SET n.summary = $s", {"id": row["id"], "s": hit["summary"]})
            continue
        todo.append((row["id"], "module" if row["kind"] in ("module", "package") else row["kind"], text, h))
    if not todo:
        print(f"nothing to describe: {len(cache)} summaries cached", file=out)
        return 0
    summarise = summarise or _summarise_with_claude(model)
    # ponytail: 8 threads, no rate-limit handling beyond the SDK's own retries
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda t: summarise(t[1], t[2]), todo))
    written = 0
    for (nid, _, _, h), summary in zip(todo, results):
        cache[nid] = {"hash": h, "summary": summary}
        if summary:
            graph.query("MATCH (n) WHERE n.id = $id SET n.summary = $s", {"id": nid, "s": summary})
            written += 1
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cache, fh, indent=0, sort_keys=True)
    print(f"described {written} of {len(todo)} nodes with {model}; {len(cache)} summaries in {path}", file=out)
    return 0
