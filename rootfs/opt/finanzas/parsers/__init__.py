"""Bank statement parsers.

Each parser exposes `sniff(path) -> bool` and `parse(path) -> ParsedStatement`
so the ingestion layer can auto-detect the source bank without user hints.
"""
from __future__ import annotations

from .types import ParsedStatement, ParsedTransaction
from . import openbank

__all__ = ["ParsedStatement", "ParsedTransaction", "detect_and_parse", "openbank"]

_PARSERS = [openbank]


def detect_and_parse(path: str) -> ParsedStatement:
    for mod in _PARSERS:
        if mod.sniff(path):
            return mod.parse(path)
    raise ValueError(f"No parser could handle {path}")
