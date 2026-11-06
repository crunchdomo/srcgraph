"""Extractor registry: file extension -> extract(rel_path, source, module_name) -> Extraction."""
from collections.abc import Callable

from srcgraph.extractors import python, terraform
from srcgraph.model import Extraction

EXTRACTORS: dict[str, Callable[[str, bytes, str], Extraction]] = {
    ".py": python.extract,
    ".tf": terraform.extract,
}
