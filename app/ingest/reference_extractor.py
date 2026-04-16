"""Extract cross-references and requirement IDs from markdown content."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .walker import IngestFile

# Match doc IDs like BRD-001, SPEC-003, DES-012, ASIS-001, etc.
# Pattern: 2-4 uppercase letters, hyphen, 1-4 digits
_DOC_ID_RE = re.compile(r"\b([A-Z]{2,4}-\d{1,4})\b")

# Match requirement IDs like REQ-FR-001, REQ-NFR-002, FR-001, NFR-003
_REQ_ID_RE = re.compile(r"\b((?:REQ-)?(?:FR|NFR|BR|SR|IR|TR)-\d{1,4})\b")

# Known doc ID prefixes (to filter out false positives like ISO-9001)
_KNOWN_PREFIXES = frozenset({
    "ASIS", "ASSP", "ASAP", "ASSC",
    "BRD", "SLA", "GAP",
    "DES", "ADR", "DM", "SD", "UXD",
    "SPEC", "IF", "INT",
    "TP", "DEV", "MIG", "DEP", "RB", "PCR", "AGT",
})

# Traceability hierarchy: doc_type -> list of upstream types it should link to
# (within the same domain)
_UPSTREAM_HIERARCHY: dict[str, list[str]] = {
    "spec":             ["design", "brd"],
    "design":           ["brd"],
    "interface":        ["spec"],
    "integration":      ["spec"],
    "data-model":       ["design"],
    "security-design":  ["design"],
    "test-plan":        ["spec"],
    "gap-analysis":     ["brd"],
    "sla-nfr":          ["brd"],
    "as-is-spec":       ["as-is"],
    "as-is-api":        ["as-is-spec"],
    "as-is-schema":     ["as-is-spec"],
}


def extract_doc_references(content: str) -> list[str]:
    """Extract doc ID references (e.g., BRD-001, SPEC-003) from markdown body."""
    matches = _DOC_ID_RE.findall(content)
    refs = []
    seen = set()
    for m in matches:
        prefix = m.rsplit("-", 1)[0]
        if prefix in _KNOWN_PREFIXES and m not in seen:
            refs.append(m)
            seen.add(m)
    return refs


def extract_req_ids(content: str) -> list[str]:
    """Extract requirement IDs (e.g., REQ-FR-001, FR-001) from markdown body."""
    matches = _REQ_ID_RE.findall(content)
    seen = set()
    ids = []
    for m in matches:
        if m not in seen:
            ids.append(m)
            seen.add(m)
    return ids


def infer_links_from_hierarchy(files: list[IngestFile]) -> None:
    """Auto-link files within the same domain using the traceability hierarchy.

    For each file, looks for upstream doc types in the same domain and adds
    their doc_ids to links_to. Mutates files in place.
    """
    # Build domain+type -> doc_id index
    domain_type_index: dict[tuple[str, str], str] = {}
    for f in files:
        key = (f.domain, f.doc_type)
        # Keep first file per domain+type (primary)
        if key not in domain_type_index:
            domain_type_index[key] = f.doc_id

    # Link each file to its upstream types within the same domain
    for f in files:
        upstream_types = _UPSTREAM_HIERARCHY.get(f.doc_type, [])
        for upstream_type in upstream_types:
            upstream_id = domain_type_index.get((f.domain, upstream_type))
            if upstream_id and upstream_id not in f.links_to:
                f.links_to.append(upstream_id)


def enrich_references(files: list[IngestFile]) -> None:
    """Extract doc refs and req IDs from content, then infer hierarchy links.

    Mutates files in place.
    """
    for f in files:
        # Extract explicit references from content
        content_refs = extract_doc_references(f.content)
        content_reqs = extract_req_ids(f.content)

        # Add to file's links_to and req_ids (dedup)
        for ref in content_refs:
            if ref not in f.links_to and ref != f.doc_id:
                f.links_to.append(ref)
        for req in content_reqs:
            if req not in f.req_ids:
                f.req_ids.append(req)

    # Infer hierarchy links across the batch
    infer_links_from_hierarchy(files)
