"""Read source lines for excerpts. Paths are relative to a root and must stay inside it."""
import os


class Source:
    def __init__(self, root: str = "."):
        self.root = os.path.realpath(root)
        self._cache: dict[str, list[str] | None] = {}

    def lines(self, path: str) -> list[str] | None:
        """The file's lines without newlines, or None when it is missing, unreadable, or outside the root."""
        if path not in self._cache:
            full = os.path.realpath(os.path.join(self.root, path))
            lines = None
            if full == self.root or full.startswith(self.root + os.sep):
                try:
                    with open(full, "rb") as fh:
                        lines = fh.read().decode("utf-8", "replace").splitlines()
                except OSError:
                    lines = None
            self._cache[path] = lines
        return self._cache[path]
