"""Generate YAML frontmatter for ingested markdown documents."""

from __future__ import annotations

from datetime import date

# Full prefix map matching doc_generator.py
DOC_TYPE_PREFIXES: dict[str, str] = {
    "as-is": "ASIS",
    "as-is-spec": "ASSP",
    "as-is-api": "ASAP",
    "as-is-schema": "ASSC",
    "spec": "SPEC",
    "data-model": "DM",
    "interface": "IF",
    "integration": "INT",
    "design": "DES",
    "adr": "ADR",
    "security-design": "SD",
    "ux-design": "UXD",
    "brd": "BRD",
    "sla-nfr": "SLA",
    "gap-analysis": "GAP",
    "test-plan": "TP",
    "dev-log": "DEV",
    "data-migration": "MIG",
    "deployment": "DEP",
    "runbook": "RB",
    "pcr": "PCR",
    "agent-config": "AGT",
}

# Phase 0 types use discovery-specific frontmatter (source_repo, scan_date, etc.)
_PHASE0_TYPES = frozenset({"as-is", "as-is-spec", "as-is-api", "as-is-schema"})


def make_doc_id(doc_type: str, seq: int) -> str:
    """Generate a doc ID like ASIS-001, BRD-002, SPEC-003, etc."""
    prefix = DOC_TYPE_PREFIXES.get(doc_type, "DOC")
    return f"{prefix}-{seq:03d}"


def generate_frontmatter(
    doc_id: str,
    title: str,
    doc_type: str,
    tags: list[str] | None = None,
    source_dir: str = "",
    links_to: list[str] | None = None,
    req_ids: list[str] | None = None,
    status: str = "Draft",
) -> str:
    """Generate a YAML frontmatter block for any document type."""
    today = date.today().isoformat()
    tag_list = ", ".join(tags or [])
    links_list = ", ".join(links_to or [])
    req_list = ", ".join(req_ids or [])

    lines = [
        "---",
        f"doc_id: {doc_id}",
        f'title: "{_escape_yaml(title)}"',
        f"status: {status}",
        'owner: ""',
        '"generated_by": "discovery-cli:ingest-docs"',
        f"tags: [{tag_list}]",
        f"links_to: [{links_list}]",
        f"req_ids: [{req_list}]",
    ]

    if doc_type in _PHASE0_TYPES:
        # Phase 0 discovery docs get source tracking fields
        lines.extend([
            f'source_repo: "{_escape_yaml(source_dir)}"',
            'source_commit: ""',
            f'scan_date: "{today}"',
            "needs_review: true",
            "discovery_confidence: null",
        ])
    else:
        # Design/spec/requirements docs get audit tracking fields
        lines.extend([
            'last_audit_status: ""',
            'last_audit_date: ""',
            "last_audit_issues: 0",
            "needs_review: true",
        ])

    lines.extend([
        f"date: {today}",
        "---",
    ])
    return "\n".join(lines) + "\n"


def prepend_frontmatter(content: str, frontmatter: str) -> str:
    """Prepend frontmatter to markdown content, stripping any existing frontmatter."""
    stripped = _strip_existing_frontmatter(content)
    return frontmatter + "\n" + stripped


def _strip_existing_frontmatter(content: str) -> str:
    """Remove existing YAML frontmatter if present."""
    if content.startswith("---"):
        end = content.find("---", 3)
        if end != -1:
            return content[end + 3:].lstrip("\n")
    return content


def _escape_yaml(s: str) -> str:
    """Escape quotes in YAML string values."""
    return s.replace('"', '\\"')
