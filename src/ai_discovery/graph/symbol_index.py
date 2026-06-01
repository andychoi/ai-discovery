"""Precise symbol-resolution index — the LSP/compiler-index resolver tier.

The assessment's biggest architectural recommendation: adopt LSP / compiler-
frontend / SCIP / stack-graphs symbol resolution as a high-confidence tier ABOVE
the name heuristics, because heuristic resolution fundamentally cannot resolve
interface / polymorphic dispatch (it has no type system). This is the *consumer*
side: when an authoritative index is present, `build_call_graph` uses it as
Stage 0 (confidence 1.0); when absent, it falls through to the existing
heuristic stages unchanged — so the feature is purely additive.

Index format (`symbol_index.json` at the repo root or scan output dir) — a flat
edge list that any precise indexer (a SCIP/LSIF exporter, a compiler plugin, or a
DI-config-aware tool) can emit:

    {
      "version": 1,
      "tool": "scip",
      "edges": [
        {"caller": "com.x.Checkout.run", "call": "process",
         "receiver": "processor", "callee": "com.x.StripeProcessor.process"}
      ]
    }

`receiver` is optional; when present it disambiguates multiple calls of the same
name from one caller. We deliberately keep the format tool-agnostic rather than
parsing SCIP protobuf directly, so any indexer can feed it.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_INDEX_FILENAMES = ("symbol_index.json", ".scip.json", "scip_index.json")


@dataclass
class SymbolIndex:
    # (caller_qn, call_name) -> list of {"receiver": str|None, "callee": str}
    entries: dict[tuple[str, str], list[dict]] = field(default_factory=dict)
    tool: str = ""

    def resolve(self, caller: str, call_name: str, receiver: str | None) -> str | None:
        """Return the authoritative callee qualified_name, or None if not indexed.

        Prefers a receiver-specific entry, then a receiver-agnostic one, then the
        first — so `processor.process()` resolves to the exact impl the indexer
        recorded even when several calls of `process` leave the same caller.
        """
        cands = self.entries.get((caller, call_name))
        if not cands:
            return None
        if receiver:
            for c in cands:
                if c.get("receiver") and c["receiver"] == receiver:
                    return c["callee"]
        for c in cands:
            if not c.get("receiver"):
                return c["callee"]
        return cands[0]["callee"]


def load_symbol_index(path) -> SymbolIndex | None:
    """Load a symbol index from a file or a directory containing one. Returns
    None when no index is present (the common case — resolution stays heuristic)."""
    p = Path(path)
    index_file = None
    if p.is_file():
        index_file = p
    elif p.is_dir():
        for name in _INDEX_FILENAMES:
            cand = p / name
            if cand.exists():
                index_file = cand
                break
    if index_file is None:
        return None
    try:
        doc = json.loads(index_file.read_text(encoding="utf-8", errors="ignore"))
    except Exception as exc:
        logger.warning("Failed to parse symbol index %s: %s", index_file, exc)
        return None
    edges = doc.get("edges") if isinstance(doc, dict) else None
    if not isinstance(edges, list):
        return None
    entries: dict[tuple[str, str], list[dict]] = {}
    for e in edges:
        if not isinstance(e, dict):
            continue
        caller, call, callee = e.get("caller"), e.get("call"), e.get("callee")
        if not (caller and call and callee):
            continue
        entries.setdefault((caller, call), []).append(
            {"receiver": e.get("receiver"), "callee": callee}
        )
    if not entries:
        return None
    return SymbolIndex(entries=entries, tool=str(doc.get("tool", "")))
