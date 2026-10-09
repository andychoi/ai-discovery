"""Source decoding with legacy-encoding fallback (roadmap item M0.4).

Korean enterprise code is often CP949/EUC-KR. Parsers used to decode strictly
as UTF-8 (or with ``errors="ignore"``), which either dropped the whole file or
silently deleted Hangul. ``read_source`` decodes once, in a fixed order, and
returns **UTF-8 bytes** so tree-sitter byte offsets and every downstream
``.decode()`` stay consistent.

Order:
  1. BOM sniff (UTF-8-sig, UTF-16 LE/BE)
  2. Strict UTF-8 (valid UTF-8 is a stronger signal than a possibly stale
     header — legacy Korean bytes essentially never validate as UTF-8)
  3. Declared charset hint in the file head (JSP ``pageEncoding``/``charset=``,
     ``<?xml encoding=?>``, ``<meta charset>``, Python ``coding:`` cookie)
  4. Legacy candidates (default ``cp949`` then ``euc-kr``; CP949 is a superset)
  5. UTF-8 with ``errors="replace"`` — ``lossy=True``, never a silent drop

Per-process counters (``fallback_stats()``) let the pipeline report how many
files needed a fallback.
"""

from __future__ import annotations

import codecs
import re
import threading
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

DEFAULT_LEGACY_ENCODINGS: tuple[str, ...] = ("cp949", "euc-kr")

_HINT_RE = re.compile(
    rb"""(?:pageEncoding\s*=\s*["']?|charset\s*=\s*["']?|<\?xml[^>]*encoding\s*=\s*["']|coding[:=]\s*)
         ([A-Za-z0-9_.:-]+)""",
    re.IGNORECASE | re.VERBOSE,
)
_HEAD_BYTES = 4096

_lock = threading.Lock()
_fallback_files: dict[str, str] = {}  # resolved path -> encoding label (distinct files)


@dataclass(frozen=True)
class DecodedSource:
    data: bytes          # UTF-8 encoded source
    encoding: str        # encoding actually used to decode the original bytes
    lossy: bool = False  # True only for the last-resort replace decode

    @property
    def is_fallback(self) -> bool:
        # A UTF-8 BOM is ordinary (common in .NET repos), not a fallback.
        return self.encoding not in ("utf-8", "utf-8-sig", "ascii") or self.lossy

    @property
    def text(self) -> str:
        return self.data.decode("utf-8")


def _normalize(name: str) -> str | None:
    try:
        return codecs.lookup(name).name
    except LookupError:
        return None


def _declared_encoding(raw: bytes) -> str | None:
    match = _HINT_RE.search(raw[:_HEAD_BYTES])
    if not match:
        return None
    return _normalize(match.group(1).decode("ascii", "ignore"))


def decode_bytes(raw: bytes, legacy: tuple[str, ...] = DEFAULT_LEGACY_ENCODINGS) -> DecodedSource:
    """Decode *raw* per the module order and return UTF-8 bytes."""
    if raw.startswith(codecs.BOM_UTF8):
        return DecodedSource(raw[len(codecs.BOM_UTF8):].decode("utf-8", "replace").encode("utf-8"), "utf-8-sig")
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return DecodedSource(raw.decode("utf-16").encode("utf-8"), "utf-16")

    candidates: list[str] = ["utf-8"]
    declared = _declared_encoding(raw)
    if declared:
        candidates.append(declared)
    candidates.extend(e for e in (_normalize(n) for n in legacy) if e)

    seen: set[str] = set()
    for enc in candidates:
        if enc in seen:
            continue
        seen.add(enc)
        try:
            text = raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        return DecodedSource(raw if enc == "utf-8" else text.encode("utf-8"), enc)

    return DecodedSource(raw.decode("utf-8", "replace").encode("utf-8"), "utf-8", lossy=True)


def read_source(path: Path | str, legacy: tuple[str, ...] = DEFAULT_LEGACY_ENCODINGS) -> DecodedSource:
    """Read *path* and return its source as UTF-8 bytes (see module docstring)."""
    decoded = decode_bytes(Path(path).read_bytes(), legacy)
    if decoded.is_fallback:
        with _lock:
            _fallback_files[str(Path(path).resolve())] = "lossy" if decoded.lossy else decoded.encoding
    return decoded


def encoding_hints(decoded: DecodedSource) -> dict:
    """Keys to merge into ``CodeNode.framework_hints`` (empty for plain UTF-8)."""
    if not decoded.is_fallback:
        return {}
    hints: dict = {"source_encoding": decoded.encoding}
    if decoded.lossy:
        hints["source_encoding_lossy"] = True
    return hints


def fallback_stats() -> dict[str, int]:
    """Distinct files decoded via a fallback, counted by encoding label."""
    with _lock:
        return dict(Counter(_fallback_files.values()))


def reset_fallback_stats() -> None:
    with _lock:
        _fallback_files.clear()
