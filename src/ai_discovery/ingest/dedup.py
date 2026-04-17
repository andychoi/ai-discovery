"""Staleness detection and TF-IDF duplicate detection for ingested documents."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .walker import IngestFile

# ── Staleness detection ──────────────────────────────────────────────────────

# Filename patterns indicating an older version
_VERSION_RE = re.compile(r"[-_]v(\d+)", re.IGNORECASE)
_DATE_RE = re.compile(r"(\d{4}[-_]?\d{2}[-_]?\d{2})")
_STALE_FILENAME_PATTERNS = re.compile(
    r"[-_](old|draft|backup|archived|deprecated|obsolete|prev|original)\b",
    re.IGNORECASE,
)

# Content patterns indicating staleness
_STALE_CONTENT_PATTERNS = [
    re.compile(r"\b(?:deprecated|obsolete)\b", re.IGNORECASE),
    re.compile(r"\bsuperseded\s+by\b", re.IGNORECASE),
    re.compile(r"\breplaced\s+by\b", re.IGNORECASE),
    re.compile(r"\bno\s+longer\s+(?:valid|applicable|in\s+use)\b", re.IGNORECASE),
    re.compile(r"\b(?:archived|out\s+of\s+date)\b", re.IGNORECASE),
]


def detect_stale_files(files: list[IngestFile]) -> None:
    """Flag files with staleness signals. Mutates IngestFile.stale and .stale_reason."""
    # Group files by base name (strip version suffixes) to detect version clusters
    base_name_groups: dict[str, list[IngestFile]] = defaultdict(list)
    for f in files:
        base = _normalize_base_name(f.path.stem)
        base_name_groups[base].append(f)

    for f in files:
        reasons: list[str] = []

        # Check filename patterns
        if _STALE_FILENAME_PATTERNS.search(f.path.stem):
            reasons.append(f"filename contains stale indicator: {f.path.stem}")

        # Check content patterns
        for pattern in _STALE_CONTENT_PATTERNS:
            if pattern.search(f.content):
                reasons.append(f"content matches: {pattern.pattern}")
                break  # one content match is enough

        # Check version clustering: if there's a newer version of this file
        base = _normalize_base_name(f.path.stem)
        siblings = base_name_groups.get(base, [])
        if len(siblings) > 1:
            my_version = _extract_version(f.path.stem)
            max_version = max(_extract_version(s.path.stem) for s in siblings)
            if my_version < max_version:
                reasons.append(f"older version (v{my_version} < v{max_version})")

        if reasons:
            f.stale = True
            f.stale_reason = "; ".join(reasons)


def _normalize_base_name(stem: str) -> str:
    """Strip version suffixes and date stamps to get canonical base name."""
    name = _VERSION_RE.sub("", stem)
    name = _DATE_RE.sub("", name)
    name = _STALE_FILENAME_PATTERNS.sub("", name)
    name = re.sub(r"[-_]+$", "", name)  # trailing separators
    return name.lower()


def _extract_version(stem: str) -> int:
    """Extract version number from filename, or 0 if none."""
    match = _VERSION_RE.search(stem)
    return int(match.group(1)) if match else 0


# ── Duplicate detection ──────────────────────────────────────────────────────

@dataclass
class DuplicateGroup:
    """A group of near-duplicate documents."""
    primary: IngestFile          # the file to keep as Draft
    duplicates: list[IngestFile] = field(default_factory=list)  # marked Deprecated
    similarity: float = 0.0      # max pairwise similarity in group


def detect_duplicates(
    files: list[IngestFile],
    threshold: float = 0.85,
) -> list[DuplicateGroup]:
    """Find near-duplicate files using TF-IDF + cosine similarity.

    Groups files within each doc_type. Files in a duplicate group (except the
    primary) get marked with duplicate_of and stale=True.

    Returns list of duplicate groups found.
    """
    if len(files) < 2:
        return []

    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
    except ImportError:
        # scikit-learn not installed — skip dedup
        return []

    # Group by doc_type to only compare within same type
    by_type: dict[str, list[IngestFile]] = defaultdict(list)
    for f in files:
        by_type[f.doc_type].append(f)

    all_groups: list[DuplicateGroup] = []

    for doc_type, type_files in by_type.items():
        if len(type_files) < 2:
            continue

        # Build TF-IDF matrix
        texts = [f.content for f in type_files]
        try:
            vectorizer = TfidfVectorizer(
                max_features=5000,
                stop_words="english",
                strip_accents="unicode",
            )
            tfidf_matrix = vectorizer.fit_transform(texts)
            sim_matrix = cosine_similarity(tfidf_matrix)
        except Exception:
            continue

        # Find pairs above threshold using union-find for transitive grouping
        n = len(type_files)
        parent = list(range(n))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: int, b: int) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        max_sim: dict[tuple[int, int], float] = {}
        for i in range(n):
            for j in range(i + 1, n):
                if sim_matrix[i, j] >= threshold:
                    union(i, j)
                    key = (min(i, j), max(i, j))
                    max_sim[key] = sim_matrix[i, j]

        # Build groups
        groups: dict[int, list[int]] = defaultdict(list)
        for i in range(n):
            groups[find(i)].append(i)

        for members in groups.values():
            if len(members) < 2:
                continue

            # Pick primary: largest file (most content), then newest mtime
            member_files = [type_files[i] for i in members]
            primary = max(member_files, key=lambda f: (len(f.content), f.path.stat().st_mtime))
            duplicates = [f for f in member_files if f is not primary]

            # Compute max similarity in group
            group_sim = 0.0
            for i in members:
                for j in members:
                    if i < j:
                        group_sim = max(group_sim, sim_matrix[i, j])

            # Mark duplicates
            for dup in duplicates:
                dup.duplicate_of = str(primary.path)
                dup.stale = True
                dup.stale_reason = f"duplicate of {primary.path.name} (similarity: {group_sim:.2f})"

            all_groups.append(DuplicateGroup(
                primary=primary,
                duplicates=duplicates,
                similarity=group_sim,
            ))

    return all_groups


# ── Split suggestions ────────────────────────────────────────────────────────

@dataclass
class SplitSuggestion:
    file: IngestFile
    detected_types: list[str]
    reason: str


def detect_splittable(files: list[IngestFile]) -> list[SplitSuggestion]:
    """Flag very large files that have strong signals for multiple doc types."""
    from .classifier import _TYPE_KEYWORDS, _HEADING_RE

    suggestions: list[SplitSuggestion] = []
    for f in files:
        if len(f.content) < 5000:  # only check large files
            continue

        headings = [m.group(1).lower().strip() for m in _HEADING_RE.finditer(f.content)]
        heading_text = " ".join(headings)

        strong_types: list[str] = []
        for doc_type, keywords in _TYPE_KEYWORDS.items():
            score = sum(3 for kw in keywords if kw in heading_text)
            if score >= 6:  # at least 2 heading matches
                strong_types.append(doc_type)

        if len(strong_types) >= 2:
            suggestions.append(SplitSuggestion(
                file=f,
                detected_types=strong_types,
                reason=f"Large file ({len(f.content)} chars) with signals for: {', '.join(strong_types)}",
            ))

    return suggestions
