"""Pure functions that build a quality summary from a scan's artifacts.

The server layer (server.py) only does HTTP and template rendering; all the
logic that could be wrong lives here so it can be unit-tested in isolation.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

# Quality thresholds pulled from CLAUDE.md "Key Metrics & Targets".
# Edges at or above HIGH_CONF_THRESHOLD count as confidently resolved;
# edges below LOW_CONF_THRESHOLD are the triage pile.
HIGH_CONF_THRESHOLD = 0.85
LOW_CONF_THRESHOLD = 0.65
MAX_LOW_CONF_RATIO = 0.15

# The canonical artifacts written by `discover scan` — presence of each
# indicates the corresponding phase completed.
_ARTIFACTS: tuple[tuple[str, str, str], ...] = (
    ("db", "discovery-{slug}.db", "SQLite — nodes, edges, docs, costs"),
    ("fsms", "entity_state_machines.json", "Phase 3 FSM backbone"),
    ("cross_links", "cross_entity_transitions.json", "Phase 3b/3.1c cross-entity links"),
    ("conditions", "entity_conditions.json", "Phase 3d guard correlations"),
    ("backbone_mmd", "entity_backbone.mmd", "Mermaid L1/L2 diagram"),
    ("decisions_md", "entity_decisions.md", "Decision log"),
    ("ears_md", "entity_ears.md", "EARS requirement summary"),
)
_ARTIFACT_DIRS: tuple[tuple[str, str], ...] = (
    ("bpmn", "bpmn"),
    ("dmn", "dmn"),
    ("ears", "ears"),
    ("mermaid", "mermaid"),
)


@dataclass(frozen=True)
class Artifact:
    key: str
    path: Path
    description: str
    exists: bool
    # For directories: how many files inside. None for single-file artifacts.
    file_count: Optional[int] = None


@dataclass(frozen=True)
class ConfidenceBucket:
    label: str            # e.g. "0.8–0.9"
    lo: float
    hi: float             # inclusive upper edge on the last bucket only
    count: int


@dataclass(frozen=True)
class QualityStatus:
    total_edges: int
    resolved_edges: int                # confidence >= HIGH_CONF_THRESHOLD
    low_conf_edges: int                # confidence < LOW_CONF_THRESHOLD
    resolution_ratio: float            # resolved / total
    low_conf_ratio: float              # low-conf / total
    meets_resolution_target: bool      # ratio >= 0.85
    meets_low_conf_target: bool        # ratio <= 0.15


@dataclass(frozen=True)
class DocRow:
    doc_id: str
    title: str
    doc_type: str
    domain: Optional[str]
    confidence: Optional[float]
    unverified_claims: int
    bucket: Optional[str]              # ASIS / ASD / ASSC / PF, if we can map it
    rel_path: Optional[str]            # path under docs_root/<slug>/, if present


@dataclass(frozen=True)
class DocTreeEntry:
    title: str
    rel_path: str                      # "ASIS/foo-as-is.md"
    confidence: Optional[float]
    unverified_claims: Optional[int]


@dataclass(frozen=True)
class DashboardData:
    slug: str
    output_dir: Path
    docs_root: Path
    db_path: Optional[Path]
    artifacts: list[Artifact]
    histogram: list[ConfidenceBucket]
    quality: Optional[QualityStatus]
    weakest_docs: list[DocRow]
    doc_tree: dict[str, list[DocTreeEntry]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Artifact presence
# ---------------------------------------------------------------------------

def discover_artifacts(output_dir: Path, slug: str) -> list[Artifact]:
    """Inventory the canonical artifacts under ``output_dir/slug``."""
    base = output_dir / slug
    result: list[Artifact] = []
    for key, name_tpl, desc in _ARTIFACTS:
        path = base / name_tpl.format(slug=slug)
        result.append(Artifact(key=key, path=path, description=desc, exists=path.exists()))
    for key, dirname in _ARTIFACT_DIRS:
        path = base / dirname
        exists = path.is_dir()
        count = sum(1 for _ in path.iterdir()) if exists else 0
        result.append(Artifact(
            key=key, path=path, description=f"{dirname}/ directory",
            exists=exists, file_count=count,
        ))
    return result


# ---------------------------------------------------------------------------
# SQL-backed summaries
# ---------------------------------------------------------------------------

def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def confidence_histogram(db_path: Path) -> list[ConfidenceBucket]:
    """Ten equal-width buckets over [0.0, 1.0]. Last bucket is inclusive of 1.0."""
    edges: list[tuple[float, float]] = [(i / 10, (i + 1) / 10) for i in range(10)]
    buckets: list[ConfidenceBucket] = []
    with _connect(db_path) as conn:
        for lo, hi in edges:
            # Last bucket includes 1.0; others are [lo, hi).
            is_last = hi >= 1.0
            if is_last:
                cnt = conn.execute(
                    "SELECT COUNT(*) FROM call_edges WHERE confidence >= ?", (lo,),
                ).fetchone()[0]
            else:
                cnt = conn.execute(
                    "SELECT COUNT(*) FROM call_edges WHERE confidence >= ? AND confidence < ?",
                    (lo, hi),
                ).fetchone()[0]
            label = f"{lo:.1f}–{hi:.1f}"
            buckets.append(ConfidenceBucket(label=label, lo=lo, hi=hi, count=cnt))
    return buckets


def quality_status(db_path: Path) -> Optional[QualityStatus]:
    """Pass/fail vs CLAUDE.md targets. Returns None if there are no edges."""
    with _connect(db_path) as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM call_edges WHERE confidence IS NOT NULL"
        ).fetchone()[0]
        if total == 0:
            return None
        resolved = conn.execute(
            "SELECT COUNT(*) FROM call_edges WHERE confidence >= ?",
            (HIGH_CONF_THRESHOLD,),
        ).fetchone()[0]
        low = conn.execute(
            "SELECT COUNT(*) FROM call_edges WHERE confidence < ?",
            (LOW_CONF_THRESHOLD,),
        ).fetchone()[0]
    resolution_ratio = resolved / total
    low_conf_ratio = low / total
    return QualityStatus(
        total_edges=total,
        resolved_edges=resolved,
        low_conf_edges=low,
        resolution_ratio=resolution_ratio,
        low_conf_ratio=low_conf_ratio,
        meets_resolution_target=resolution_ratio >= 0.85,
        meets_low_conf_target=low_conf_ratio <= MAX_LOW_CONF_RATIO,
    )


def weakest_docs(db_path: Path, limit: int = 20) -> list[DocRow]:
    """Docs most likely to need human review.

    Rank by (unverified_claims DESC, confidence ASC). Docs with no confidence
    score sort last so the front of the list is always actionable.
    """
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT doc_id, title, doc_type, domain, confidence, unverified_claims
            FROM generated_docs
            ORDER BY
                COALESCE(unverified_claims, 0) DESC,
                CASE WHEN confidence IS NULL THEN 1 ELSE 0 END ASC,
                confidence ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [
        DocRow(
            doc_id=r["doc_id"] or "",
            title=r["title"] or r["doc_id"] or "(untitled)",
            doc_type=r["doc_type"] or "",
            domain=r["domain"],
            confidence=r["confidence"],
            unverified_claims=r["unverified_claims"] or 0,
            bucket=_bucket_from_doc_type(r["doc_type"]),
            rel_path=None,     # Filled in by caller if docs_root is walked.
        )
        for r in rows
    ]


def _bucket_from_doc_type(doc_type: Optional[str]) -> Optional[str]:
    # Reuse the canonical map from the doc generator so buckets stay aligned
    # with the directory names it actually writes (ASIS, ASD, ASSC, SPEC, PF …).
    if not doc_type:
        return None
    from ai_discovery.generators.doc_generator import _DOC_TYPE_PREFIXES
    return _DOC_TYPE_PREFIXES.get(doc_type.lower().replace("_", "-"))


# ---------------------------------------------------------------------------
# Markdown docs tree (docs_root/<slug>/<BUCKET>/*.md)
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_FM_FIELD_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*?)\s*$", re.MULTILINE)


def _read_frontmatter(path: Path) -> dict[str, str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}
    out: dict[str, str] = {}
    for fm in _FM_FIELD_RE.finditer(m.group(1)):
        out[fm.group(1)] = fm.group(2).strip().strip('"').strip("'")
    return out


def doc_tree(docs_root: Path, slug: str) -> dict[str, list[DocTreeEntry]]:
    """Walk ``docs_root/<slug>/<BUCKET>/*.md`` and extract frontmatter hints."""
    project_root = docs_root / slug
    if not project_root.is_dir():
        return {}
    tree: dict[str, list[DocTreeEntry]] = {}
    for bucket_dir in sorted(project_root.iterdir()):
        if not bucket_dir.is_dir():
            continue
        entries: list[DocTreeEntry] = []
        for md in sorted(bucket_dir.glob("*.md")):
            fm = _read_frontmatter(md)
            conf = _parse_float(fm.get("discovery_confidence"))
            unv = _parse_int(fm.get("unverified_claims"))
            entries.append(DocTreeEntry(
                title=fm.get("title") or md.stem,
                rel_path=f"{bucket_dir.name}/{md.name}",
                confidence=conf,
                unverified_claims=unv,
            ))
        if entries:
            tree[bucket_dir.name] = entries
    return tree


def _parse_float(v: Optional[str]) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _parse_int(v: Optional[str]) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Top-level aggregator
# ---------------------------------------------------------------------------
# A-5: node drill-down + search — the dashboard answers "is the scan good?";
# these answer "what does this code do?". Latest-scan scoped: qualified_name
# is only unique per scan, so unscoped queries would surface stale duplicates.
# ---------------------------------------------------------------------------

def _latest_scan_id(conn: sqlite3.Connection) -> Optional[int]:
    row = conn.execute("SELECT MAX(id) FROM scan_runs").fetchone()
    return row[0] if row else None


def search_nodes(db_path: Path, query: str, limit: int = 20) -> list[dict]:
    """Case-insensitive substring search over symbols and file paths.

    Returns [{qualified_name, name, node_type, file_path, domain, line_start}]
    from the latest scan, ordered by qualified_name. Empty/blank queries
    return [] (no accidental full-table dumps into the UI).
    """
    q = (query or "").strip()
    if not q:
        return []
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped}%"
    with _connect(db_path) as conn:
        scan_id = _latest_scan_id(conn)
        if scan_id is None:
            return []
        rows = conn.execute(
            r"""
            SELECT qualified_name, name, node_type, file_path, domain, line_start
            FROM code_nodes
            WHERE scan_id = ?
              AND (qualified_name LIKE ? ESCAPE '\' OR file_path LIKE ? ESCAPE '\')
            ORDER BY qualified_name
            LIMIT ?
            """,
            (scan_id, pattern, pattern, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def node_detail(db_path: Path, qualified_name: str) -> Optional[dict]:
    """Everything the drill-down page needs for one node, latest scan:

    metadata + source snippet, the Tier-1 summary (None when unsummarized —
    honesty over filler), and incoming/outgoing call edges carrying confidence
    AND resolution-stage provenance (`resolved_by`).
    """
    with _connect(db_path) as conn:
        scan_id = _latest_scan_id(conn)
        if scan_id is None:
            return None
        node = conn.execute(
            """
            SELECT id, qualified_name, name, node_type, language, file_path,
                   line_start, line_end, domain, source_code
            FROM code_nodes WHERE scan_id = ? AND qualified_name = ?
            """,
            (scan_id, qualified_name),
        ).fetchone()
        if node is None:
            return None

        summary_row = conn.execute(
            "SELECT purpose, business_rules, io_summary, tech_debt_signals "
            "FROM node_summaries WHERE node_id = ?",
            (node["id"],),
        ).fetchone()

        edges_out = conn.execute(
            """
            SELECT callee.qualified_name AS callee, ce.callee_name,
                   ce.edge_type, ce.confidence, ce.resolved_by
              FROM call_edges ce
         LEFT JOIN code_nodes callee ON callee.id = ce.callee_id
             WHERE ce.scan_id = ? AND ce.caller_id = ?
          ORDER BY ce.confidence DESC, ce.callee_name
            """,
            (scan_id, node["id"]),
        ).fetchall()

        edges_in = conn.execute(
            """
            SELECT caller.qualified_name AS caller,
                   ce.edge_type, ce.confidence, ce.resolved_by
              FROM call_edges ce
              JOIN code_nodes caller ON caller.id = ce.caller_id
             WHERE ce.scan_id = ? AND ce.callee_id = ?
          ORDER BY ce.confidence DESC, caller
            """,
            (scan_id, node["id"]),
        ).fetchall()

    detail = {k: node[k] for k in node.keys() if k != "id"}
    detail["summary"] = dict(summary_row) if summary_row else None
    detail["edges_out"] = [dict(r) for r in edges_out]
    detail["edges_in"] = [dict(r) for r in edges_in]
    return detail


# ---------------------------------------------------------------------------

def build_summary(
    output_dir: Path,
    docs_root: Path,
    slug: str,
) -> DashboardData:
    artifacts = discover_artifacts(output_dir, slug)
    db_artifact = next((a for a in artifacts if a.key == "db"), None)
    db_path = db_artifact.path if db_artifact and db_artifact.exists else None

    histogram: list[ConfidenceBucket] = []
    quality: Optional[QualityStatus] = None
    weakest: list[DocRow] = []
    if db_path is not None:
        histogram = confidence_histogram(db_path)
        quality = quality_status(db_path)
        weakest = weakest_docs(db_path)

    tree = doc_tree(docs_root, slug)

    return DashboardData(
        slug=slug,
        output_dir=output_dir,
        docs_root=docs_root,
        db_path=db_path,
        artifacts=artifacts,
        histogram=histogram,
        quality=quality,
        weakest_docs=weakest,
        doc_tree=tree,
    )


__all__ = [
    "Artifact",
    "ConfidenceBucket",
    "QualityStatus",
    "DocRow",
    "DocTreeEntry",
    "DashboardData",
    "HIGH_CONF_THRESHOLD",
    "LOW_CONF_THRESHOLD",
    "MAX_LOW_CONF_RATIO",
    "build_summary",
    "confidence_histogram",
    "discover_artifacts",
    "doc_tree",
    "node_detail",
    "quality_status",
    "search_nodes",
    "weakest_docs",
]
