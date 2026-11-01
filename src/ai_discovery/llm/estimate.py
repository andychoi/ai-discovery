"""Pre-run LLM cost estimate (roadmap item M1.11.b; consumed by the M2.4 cost gate).

``estimate(config, shape)`` prices the LLM stages of a ``--prose`` scan from the
shape of the repository (how many Tier-1 chunks, domains, entry-point
scenarios, screens) and the models each role resolves to under the config's
``quality``. ``shape_from_db`` reads that shape from a finished structure-only
scan, so the gate can run ``scan`` keyless first and then quote the prose cost.

The per-call token sizes below are **heuristics, not calibrated** — no
measured prose run with recorded spend exists yet (the M0.12 microblog run
was stopped before costs were written). The range is therefore wide:
``low = 0.5 × expected``, ``high = 2 × expected``. Recalibrate from
``llm_costs`` once real prose scans have been recorded. Not modelled:
embeddings (cents), response-cache hits and prompt-cache discounts (both only
lower the cost), retries.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .cost import Usage, cost_usd, price_for

# (input tokens, output tokens) per call — see module docstring.
_T1_PER_CHUNK_OUT = 200
_T1_PROMPT_OVERHEAD = 250
_T1_BATCH_SIZE = 8                 # chunks per semantic batch (typical community size)
_SCREEN_CALL = (3000, 1500)
_DOMAIN_FLOW_CALL = (6000, 2000)
_SCENARIO_CALLS = 3                # steps + IPO + interfaces
_SCENARIO_CALL = (1500, 500)
_ROLLUP_CALL = (8000, 3000)        # per domain × doc type
_REVIEW_PER_DOC = (6000 + 10 * 1500, 1500 + 10 * 50)   # claim extraction + ~10 verifications
_ONBOARD_CALL = (4000, 1500)
RANGE_LOW, RANGE_HIGH = 0.5, 2.0


@dataclass
class ScanShape:
    tier1_chunks: int = 0
    avg_chunk_tokens: int = 400
    domains: int = 0
    scenarios: int = 0
    screens: int = 0
    doc_types: int = 6


@dataclass
class StageEstimate:
    stage: str
    role: str
    model: str
    calls: int
    input_tokens: int
    output_tokens: int
    usd: float


@dataclass
class Estimate:
    expected_usd: float
    low_usd: float
    high_usd: float
    stages: list[StageEstimate] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"expected_usd": round(self.expected_usd, 4), "low_usd": round(self.low_usd, 4),
                "high_usd": round(self.high_usd, 4), "notes": self.notes,
                "stages": [s.__dict__ | {"usd": round(s.usd, 4)} for s in self.stages]}


def _stage(config: Any, stage: str, role: str, calls: int, per_in: int, per_out: int) -> StageEstimate:
    model = config.get_model(role)
    tin, tout = calls * per_in, calls * per_out
    usd = cost_usd(Usage(tin, tout), price_for(model, config.provider))
    return StageEstimate(stage, role, model, calls, tin, tout, usd)


def estimate(config: Any, shape: ScanShape) -> Estimate:
    """Expected cost and range of the LLM stages for *shape* under *config*."""
    stages: list[StageEstimate] = []
    chunk_in = shape.avg_chunk_tokens + _T1_PROMPT_OVERHEAD
    if getattr(config, "semantic_batching", False) and shape.tier1_chunks:
        calls = math.ceil(shape.tier1_chunks / _T1_BATCH_SIZE)
        per_in = (_T1_BATCH_SIZE * shape.avg_chunk_tokens) + _T1_PROMPT_OVERHEAD
        stages.append(_stage(config, "summaries", "fast", calls, per_in, _T1_BATCH_SIZE * _T1_PER_CHUNK_OUT))
    else:
        stages.append(_stage(config, "summaries", "fast", shape.tier1_chunks, chunk_in, _T1_PER_CHUNK_OUT))
    stages.append(_stage(config, "screens", "screen", shape.screens, *_SCREEN_CALL))
    stages.append(_stage(config, "flows", "standard", shape.domains, *_DOMAIN_FLOW_CALL))
    stages.append(_stage(config, "scenario_flows", "standard", shape.scenarios * _SCENARIO_CALLS, *_SCENARIO_CALL))
    docs = shape.domains * shape.doc_types
    stages.append(_stage(config, "rollup", "deep", docs, *_ROLLUP_CALL))
    stages.append(_stage(config, "review", "fast", docs, *_REVIEW_PER_DOC))
    stages.append(_stage(config, "onboarding", "standard", shape.domains, *_ONBOARD_CALL))
    expected = sum(s.usd for s in stages)
    notes = ["heuristic per-call token sizes, not calibrated; range is 0.5×–2× the expected value",
             "response-cache hits and prompt caching are not subtracted (they only lower the cost)"]
    if expected == 0 and any(s.calls for s in stages):
        notes.append(f"provider {config.provider!r} is local: $0 (time, not money, is the cost)")
    return Estimate(expected, expected * RANGE_LOW, expected * RANGE_HIGH, stages, notes)


def shape_from_db(db_path: Path, scan_id: int) -> ScanShape:
    """Shape of a finished (structure-only is enough) scan.

    Tier-1 chunks ≈ summarizable nodes outside test files; scenarios = the entry
    points ``graph/entry_points.py`` scores from the stored nodes and edges (one
    execution slice each; slices are not persisted, and parse-time base types are
    not stored, so base-type contracts are not seen here).
    """
    from ..ai.rollup import DOC_TYPES
    from ..ai.summarizer import _SUMMARIZE_TYPES, _is_test_chunk

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        repo = (conn.execute("SELECT repo_path FROM scan_runs WHERE id = ?", (scan_id,)).fetchone() or [None])[0]
        marks = ",".join("?" * len(_SUMMARIZE_TYPES))
        rows = conn.execute(
            f"SELECT file_path, LENGTH(source_code) FROM code_nodes WHERE scan_id = ? AND node_type IN ({marks})",
            (scan_id, *sorted(_SUMMARIZE_TYPES))).fetchall()
        # same repo-relative test filter as Tier 1 (a checkout under …/tests/… is not all tests, audit #90)
        kept = [length or 0 for path, length in rows if not _is_test_chunk(path, repo)]
        n = len(kept)
        avg_chars = (sum(kept) / n) if n else None
        domains = conn.execute("SELECT COUNT(*) FROM domains WHERE scan_id = ?", (scan_id,)).fetchone()[0]
        scenarios = _scenario_count(conn, scan_id)
        screens = conn.execute("SELECT COUNT(*) FROM screen_specs WHERE scan_id = ?", (scan_id,)).fetchone()[0]
    finally:
        conn.close()
    avg_tokens = max(50, min(1500, int((avg_chars or 1600) / 4)))
    return ScanShape(tier1_chunks=n, avg_chunk_tokens=avg_tokens, domains=domains, scenarios=scenarios,
                     screens=screens, doc_types=len(DOC_TYPES))


def _json(text: str | None, default):
    import json

    try:
        value = json.loads(text) if text else default
    except ValueError:
        return default
    return value if isinstance(value, type(default)) else default


def _scenario_count(conn: sqlite3.Connection, scan_id: int) -> int:
    """Entry points scored from the stored graph (M6.12) — the number of scenario slices a scan builds."""
    from ..graph.entry_points import score_entry_points
    from ..graph.models import CallEdge, CodeNode

    nodes, by_id = [], {}
    for row in conn.execute(
            "SELECT id, file_path, language, node_type, name, qualified_name, line_start, line_end, "
            "source_code, annotations, params, framework_hints FROM code_nodes WHERE scan_id = ?", (scan_id,)):
        nodes.append(CodeNode(file_path=row[1] or "", language=row[2] or "", node_type=row[3], name=row[4],
                              qualified_name=row[5], source_code=row[8] or "", line_start=row[6] or 0,
                              line_end=row[7] or 0, annotations=_json(row[9], []), params=_json(row[10], []),
                              framework_hints=_json(row[11], {})))
        by_id[row[0]] = row[5]
    edges = [CallEdge(by_id[a], by_id[b], kind or "direct_call", conf if conf is not None else 1.0,
                      {"resolved_by": origin or ""})
             for a, b, kind, conf, origin in conn.execute(
                 "SELECT caller_id, callee_id, edge_type, confidence, resolved_by FROM call_edges "
                 "WHERE scan_id = ? AND callee_id IS NOT NULL", (scan_id,))
             if a in by_id and b in by_id]
    return len(score_entry_points(nodes, edges))
