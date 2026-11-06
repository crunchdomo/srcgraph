"""Byte offset -> 1-based line number. Extractors use node.start_byte/end_byte with this instead
of node.start_point/end_point: py-tree-sitter 0.26 corrupts the heap when thousands of Point
objects are created (interpreter crashes at exit), and byte offsets are a plain int."""
import bisect


class LineIndex:
    def __init__(self, source: bytes):
        self._starts = [0] + [i + 1 for i, b in enumerate(source) if b == 0x0A]

    def line(self, offset: int) -> int:
        return bisect.bisect_right(self._starts, offset)
