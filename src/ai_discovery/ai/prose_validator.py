"""Deterministic structural validation of LLM prose against the parsed graph.

P0-4: Tier-3 doc rollup prose is free-form LLM output. Outside the AST-derived
fact tables, nothing stops the model from inventing a file path, an endpoint, or
a class/method that does not exist in the codebase. This module is a *deterministic*
(no-LLM) backstop: it extracts the concrete, checkable references from prose —
`file:line` citations and dotted/qualified code symbols — and flags any that are
wholly foreign to the parsed `code_nodes`.

Design bias: **precision over recall.** We only flag a reference when it is
*entirely* absent from the graph (no known file basename match; no dot-segment
that is a known symbol name). Real cross-domain references and ordinary prose
abbreviations (`e.g.`, `i.e.`) are never flagged. A flagged reference is strong
evidence of fabrication, suitable for lowering confidence and warning readers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from posixpath import basename


# `path/to/File.ext:NN` — a slashed or extensioned path followed by :line.
# Requires either a slash or a 1-5 char alpha extension so we don't match prose
# like "Section 3:14". Anchored on a non-word boundary to avoid URL ports.
_FILE_CITATION_RE = re.compile(
    r"(?<![\w:/])((?:[\w.\-]+/)*[\w.\-]+\.[A-Za-z]{1,5}):(\d+)"
)

# A dotted/qualified code symbol inside backticks: at least two dot-separated
# segments, with at least one PascalCase segment (a class-looking token). This
# matches `OrderService.process`, `com.example.OrderService`, `Order.findById`
# but not `e.g.`, `i.e.`, `v1.2`, or `www.example.com` (no PascalCase segment).
_BACKTICK_RE = re.compile(r"`([^`]+)`")
_QUALIFIED_SYMBOL_RE = re.compile(r"^[\w]+(?:\.[\w]+)+$")
_PASCAL_RE = re.compile(r"^[A-Z][A-Za-z0-9]*$")


@dataclass
class KnownGraph:
    """Lookup sets derived once from the parsed code_nodes."""

    file_basenames: set[str] = field(default_factory=set)
    file_paths: set[str] = field(default_factory=set)
    symbol_names: set[str] = field(default_factory=set)  # short names (class/method)
    qualified_names: set[str] = field(default_factory=set)


@dataclass
class ProseValidation:
    unknown_files: list[str] = field(default_factory=list)
    unknown_symbols: list[str] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return not self.unknown_files and not self.unknown_symbols

    @property
    def count(self) -> int:
        return len(self.unknown_files) + len(self.unknown_symbols)


def build_known_graph(code_nodes) -> KnownGraph:
    """Build lookup sets from an iterable of objects/rows exposing
    file_path / name / qualified_name (CodeNode or sqlite Row)."""
    g = KnownGraph()
    for n in code_nodes:
        fp = _get(n, "file_path")
        if fp:
            g.file_paths.add(fp)
            g.file_basenames.add(basename(fp))
        name = _get(n, "name")
        if name:
            g.symbol_names.add(name)
        qn = _get(n, "qualified_name")
        if qn:
            g.qualified_names.add(qn)
            # every dot-segment of a qualified name is a legitimate symbol token
            for seg in qn.split("."):
                g.symbol_names.add(seg)
    return g


def validate_prose(content_md: str, known: KnownGraph) -> ProseValidation:
    """Flag file:line citations and qualified symbols absent from the graph."""
    result = ProseValidation()
    seen_files: set[str] = set()
    for path, _line in _FILE_CITATION_RE.findall(content_md):
        if path in seen_files:
            continue
        seen_files.add(path)
        if not _file_is_known(path, known):
            result.unknown_files.append(path)

    seen_syms: set[str] = set()
    for token in _BACKTICK_RE.findall(content_md):
        token = token.strip()
        if not _QUALIFIED_SYMBOL_RE.match(token):
            continue
        segments = token.split(".")
        # Must look like code: at least one PascalCase (class-looking) segment.
        if not any(_PASCAL_RE.match(s) for s in segments):
            continue
        if token in seen_syms:
            continue
        seen_syms.add(token)
        if not _symbol_is_known(token, segments, known):
            result.unknown_symbols.append(token)
    return result


def annotate(content_md: str, validation: ProseValidation) -> str:
    """Append a reviewer-facing note listing unverified references. No-op if clean."""
    if validation.is_clean:
        return content_md
    lines = ["", "---", "", "> ⚠ **Unverified code references (auto-flagged).** The "
             "following references in this document were not found in the parsed "
             "codebase and may be LLM-fabricated — verify before relying on them:"]
    for f in validation.unknown_files:
        lines.append(f"> - `{f}` (file not in scan)")
    for s in validation.unknown_symbols:
        lines.append(f"> - `{s}` (symbol not in scan)")
    return content_md + "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _get(obj, key: str):
    if isinstance(obj, dict):
        return obj.get(key)
    try:
        return obj[key]  # sqlite3.Row supports __getitem__
    except (TypeError, KeyError, IndexError):
        return getattr(obj, key, None)


def _file_is_known(path: str, known: KnownGraph) -> bool:
    if path in known.file_paths:
        return True
    base = basename(path)
    if base in known.file_basenames:
        return True
    # suffix match either direction (cited path vs known path)
    for kp in known.file_paths:
        if kp.endswith("/" + path) or path.endswith("/" + kp):
            return True
    return False


def _symbol_is_known(token: str, segments: list[str], known: KnownGraph) -> bool:
    if token in known.qualified_names:
        return True
    # qualified-name suffix/prefix overlap (cross-domain references)
    for qn in known.qualified_names:
        if qn.endswith("." + token) or token.endswith("." + qn) or token == qn:
            return True
    # Conservative fabrication test: known iff ANY dot-segment is a real symbol
    # name in the codebase. Only when EVERY segment is foreign do we flag it.
    return any(seg in known.symbol_names for seg in segments)
