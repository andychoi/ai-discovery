"""Auto-classify plain markdown files into SDLC document types."""

from __future__ import annotations

import re

_HEADING_RE = re.compile(r"^#{1,3}\s+(.+)$", re.MULTILINE)

# ── Phase 0: Discovery ──────────────────────────────────────────────────────

_ASIS_API_KW = frozenset({
    "endpoint", "api", "route", "request", "response", "rest", "graphql",
    "swagger", "openapi", "http", "grpc", "webhook", "curl",
})

_ASIS_SCHEMA_KW = frozenset({
    "schema", "table", "entity", "column", "relationship", "erd",
    "migration", "constraint", "primary key", "foreign key",
    "data dictionary", "attribute",
})

_ASIS_SPEC_KW = frozenset({
    "use case", "business rule", "processing logic",
    "precondition", "postcondition", "actor",
})

_ASIS_KW = frozenset({
    "current architecture", "tech debt", "pain point", "limitation",
    "system boundary", "as-is", "current state", "legacy",
})

# ── Phase 1: Requirements ────────────────────────────────────────────────────

_BRD_KW = frozenset({
    "business requirement", "stakeholder", "scope", "roi", "budget",
    "kpi", "success metric", "business case", "business need",
    "project objective", "business objective", "cost-benefit",
    "target audience", "market", "revenue",
})

_SLA_NFR_KW = frozenset({
    "sla", "non-functional", "latency", "uptime", "availability",
    "throughput", "rto", "rpo", "disaster recovery", "scalability",
    "performance target", "response time", "capacity", "reliability",
    "mean time", "mttr", "mtbf",
})

_GAP_ANALYSIS_KW = frozenset({
    "gap analysis", "gap assessment", "as-is vs to-be", "delta",
    "change classification", "retain", "deprecate", "impact estimate",
    "requirement change", "impact assessment", "baseline comparison",
})

# ── Phase 2: Architecture & Design ───────────────────────────────────────────

_DESIGN_KW = frozenset({
    "architecture diagram", "component design", "sequence diagram",
    "data flow", "technology stack", "design pattern",
    "high-level design", "low-level design", "system design",
    "microservice", "module design", "layer",
})

_ADR_KW = frozenset({
    "decision", "options considered", "consequences", "chosen option",
    "trade-off", "decision record", "architectural decision",
    "alternatives", "rationale", "decision driver",
})

_DATA_MODEL_KW = frozenset({
    "entity relationship", "table definition", "index strategy",
    "normalization", "pii", "pii classification", "referential integrity",
    "cardinality", "data type", "primary key", "foreign key",
    "target schema", "to-be schema",
})

_SECURITY_DESIGN_KW = frozenset({
    "threat model", "encryption", "owasp", "rbac",
    "security control", "compliance", "vulnerability", "penetration",
    "cipher", "tls", "certificate", "token management",
    "access control", "security architecture",
})

_UX_DESIGN_KW = frozenset({
    "wireframe", "mockup", "user journey", "persona", "accessibility",
    "wcag", "prototype", "interaction pattern", "user research",
    "usability", "information architecture", "navigation flow",
})

# ── Phase 3: Technical Specification ─────────────────────────────────────────

_SPEC_KW = frozenset({
    "validation rule", "error case", "edge case",
    "expected output", "input format", "output format",
    "technical specification", "functional specification",
    "processing step", "algorithm",
})

_INTERFACE_KW = frozenset({
    "endpoint definition", "request schema", "response schema",
    "rate limit", "error code", "api contract",
    "http method", "query parameter", "path parameter",
    "header", "content-type",
})

_INTEGRATION_KW = frozenset({
    "field mapping", "middleware", "etl", "message queue",
    "event bus", "webhook", "data sync", "batch job",
    "integration point", "source system", "target system",
    "transformation", "orchestration",
})

# ── Shared keywords (appear in multiple types — used for body scoring only) ──

_SHARED_REQUIREMENT_KW = frozenset({
    "requirement", "acceptance criteria", "user story",
    "functional", "scenario", "stakeholder",
})
_SHARED_ARCH_KW = frozenset({
    "architecture", "overview", "component", "dependency",
    "infrastructure", "deployment", "monitoring",
})
_SHARED_AUTH_KW = frozenset({
    "authentication", "authorization",
})

# ── Mapping: doc_type → (heading_keywords, filename_keywords) ────────────────

_TYPE_KEYWORDS: dict[str, frozenset[str]] = {
    # Phase 0
    "as-is-api":        _ASIS_API_KW,
    "as-is-schema":     _ASIS_SCHEMA_KW,
    "as-is-spec":       _ASIS_SPEC_KW,
    "as-is":            _ASIS_KW,
    # Phase 1
    "brd":              _BRD_KW,
    "sla-nfr":          _SLA_NFR_KW,
    "gap-analysis":     _GAP_ANALYSIS_KW,
    # Phase 2
    "adr":              _ADR_KW,
    "data-model":       _DATA_MODEL_KW,
    "security-design":  _SECURITY_DESIGN_KW,
    "ux-design":        _UX_DESIGN_KW,
    "design":           _DESIGN_KW,
    # Phase 3
    "interface":        _INTERFACE_KW,
    "integration":      _INTEGRATION_KW,
    "spec":             _SPEC_KW,
}

_FILENAME_HINTS: dict[str, list[str]] = {
    "as-is-api":        ["api", "endpoint", "route", "swagger"],
    "as-is-schema":     ["schema", "database", "table", "erd"],
    "as-is-spec":       ["as-is-spec"],
    "as-is":            ["as-is", "current-state", "legacy"],
    "brd":              ["brd", "business-req", "business-requirement"],
    "sla-nfr":          ["sla", "nfr", "non-functional"],
    "gap-analysis":     ["gap", "gap-analysis", "delta"],
    "design":           ["design", "architecture", "hld", "lld"],
    "adr":              ["adr", "decision"],
    "data-model":       ["data-model", "erd", "data-schema"],
    "security-design":  ["security", "threat-model", "sec-design"],
    "ux-design":        ["ux", "wireframe", "mockup", "ui-design"],
    "spec":             ["spec", "specification", "technical-spec"],
    "interface":        ["interface", "api-contract", "api-spec"],
    "integration":      ["integration", "etl", "data-flow"],
}

# Priority order for tie-breaking: more specific types first
_PRIORITY = [
    # Phase 1 (distinctive keywords, check first)
    "gap-analysis", "sla-nfr", "brd",
    # Phase 2 specific
    "adr", "security-design", "ux-design", "data-model",
    # Phase 3 specific
    "interface", "integration",
    # Phase 0 specific
    "as-is-api", "as-is-schema", "as-is-spec",
    # Broader types last (catch-all)
    "spec", "design", "as-is",
]

# All known doc types this classifier supports
ALL_DOC_TYPES = frozenset(_TYPE_KEYWORDS.keys())


def classify_markdown(content: str, filename: str = "", default: str = "as-is") -> str:
    """Classify a markdown file into one of the 15 SDLC document types.

    Uses heading keywords and body content heuristics.
    Returns a doc_type string like "brd", "design", "spec", "as-is-api", etc.
    """
    headings = [m.group(1).lower().strip() for m in _HEADING_RE.finditer(content)]
    heading_text = " ".join(headings)
    body_lower = content.lower()
    fname = filename.lower()

    scores: dict[str, int] = {dt: 0 for dt in _TYPE_KEYWORDS}

    # Score heading matches (strong signal)
    for doc_type, keywords in _TYPE_KEYWORDS.items():
        for kw in keywords:
            if kw in heading_text:
                scores[doc_type] += 3

    # Score body matches (weaker signal)
    for doc_type, keywords in _TYPE_KEYWORDS.items():
        for kw in keywords:
            if kw in body_lower:
                scores[doc_type] += 1

    # Shared keywords add to multiple types (body only, 1pt)
    for kw in _SHARED_REQUIREMENT_KW:
        if kw in body_lower:
            scores["brd"] += 1
            scores["spec"] += 1
            scores["as-is-spec"] += 1
    for kw in _SHARED_ARCH_KW:
        if kw in body_lower:
            scores["design"] += 1
            scores["as-is"] += 1
    for kw in _SHARED_AUTH_KW:
        if kw in body_lower:
            scores["security-design"] += 1
            scores["as-is-api"] += 1

    # Filename hints (strong signal)
    for doc_type, hints in _FILENAME_HINTS.items():
        if any(h in fname for h in hints):
            scores[doc_type] += 5

    # Pick highest score using priority order for tie-breaking
    best_type = default
    best_score = 0
    for dt in _PRIORITY:
        if scores.get(dt, 0) > best_score:
            best_score = scores[dt]
            best_type = dt

    # Require minimum signal for specific classification
    if best_score < 3 and best_type != default:
        return default

    return best_type
