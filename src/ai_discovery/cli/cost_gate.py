"""Cost gate and ``--dry-run`` for ``discover scan`` (roadmap item M2.4, design/cli-v1.md §3).

Before a ``--prose`` scan spends anything, the LLM cost is quoted from the *shape* of the repository
(``llm/estimate.py``) and the gate decides:

| expected spend                       | behaviour                                  |
|--------------------------------------|--------------------------------------------|
| below ``cost_gate.ask_above_usd`` ($2) | runs without asking                      |
| up to ``cost_gate.default_no_above_usd`` ($25) | asks, default **Yes**            |
| above that                           | asks, default **No**                       |
| would ask, but no TTY (or ``--json``) and no ``--yes`` | exit 2 ``cost_gate_no_tty`` |

The shape comes from the stored scan of the current commit when there is one (structure-only is enough).
Otherwise ``--dry-run`` measures it with a structure-only scan in a temporary directory (nothing is written
to the project and its lock is not taken), and a real scan runs its structure pass first — that pass is
the first half of the prose scan anyway, and its result is kept if the quote is declined.

Every gated run records the quote, the decision and the actual spend in ``cost_estimates`` (migration
0005) for calibration.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from ..core.errors import DiscoveryError
from ..llm.estimate import RANGE_HIGH, RANGE_LOW, Estimate, ScanShape, estimate, shape_from_db

RUN, ASK_YES, ASK_NO = "run", "ask_default_yes", "ask_default_no"


@dataclass
class Quote:
    estimate: Estimate
    shape: ScanShape
    source: str                 # "index" (stored scan of HEAD) | "structure_scan" (measured now)
    scan_id: Optional[int]      # the scan the shape was read from (None for a temporary scan)

    def as_dict(self) -> dict[str, Any]:
        return {"source": self.source, "shape": asdict(self.shape), **self.estimate.as_dict()}


def thresholds(cfg) -> tuple[float, float]:
    gate = getattr(cfg, "cost_gate", None)
    ask = float(getattr(gate, "ask_above_usd", 2.0))
    default_no = float(getattr(gate, "default_no_above_usd", 25.0))
    return ask, max(ask, default_no)


def verdict(usd: float, cfg) -> str:
    ask, default_no = thresholds(cfg)
    if usd < ask:
        return RUN
    return ASK_YES if usd <= default_no else ASK_NO


def interactive() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


# ── shape ────────────────────────────────────────────────────────────────────

def head_commit(repo: Path, branch: str, work_dir: Path) -> Optional[str]:
    from ..repo.resolver import resolve_repo

    try:
        return resolve_repo(str(repo), branch, work_dir).commit_sha
    except Exception:   # noqa: BLE001 — no commit means no stored shape, never a failed gate
        return None


def indexed_scan(db_path: Path, commit: Optional[str]) -> Optional[int]:
    """Newest scan of *commit* that has a graph (any status: an interrupted prose run still has its shape)."""
    if not commit or not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        row = conn.execute(
            "SELECT s.id FROM scan_runs s WHERE s.commit_sha = ? "
            "AND EXISTS (SELECT 1 FROM code_nodes n WHERE n.scan_id = s.id) ORDER BY s.id DESC LIMIT 1",
            (commit,)).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    return int(row[0]) if row else None


def completed_with_prose(db_path: Path, scan_id: Optional[int]) -> bool:
    """True when *scan_id* finished with LLM prose (re-running the same commit would spend nothing)."""
    if scan_id is None:
        return False
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = conn.execute("SELECT status, config_json FROM scan_runs WHERE id = ?", (scan_id,)).fetchone()
    except sqlite3.Error:
        return False
    finally:
        conn.close()
    if not row or row[0] != "completed":
        return False
    try:
        return not json.loads(row[1] or "{}").get("no_llm", False)
    except ValueError:
        return False


def quote_from_index(layout, cfg, branch: str) -> Optional[Quote]:
    commit = head_commit(layout.repo, branch, layout.state_dir)
    scan_id = indexed_scan(layout.db_path, commit)
    if scan_id is None:
        return None
    shape = shape_from_db(layout.db_path, scan_id)
    return Quote(estimate(_prose(cfg), shape), shape, "index", scan_id)


def quote_from_structure_scan(layout, cfg, branch: str, run: Callable[..., Any],
                              scan_id_of: Callable[[Path], Optional[int]]) -> Optional[Quote]:
    """Quote from a structure-only scan *run* has just done into *layout* (its DB holds the shape)."""
    run(layout=layout, config=_structure(cfg))
    scan_id = scan_id_of(layout.db_path)
    if scan_id is None:
        return None
    shape = shape_from_db(layout.db_path, scan_id)
    return Quote(estimate(_prose(cfg), shape), shape, "structure_scan", scan_id)


def quote_in_temp(layout, cfg, branch: str, run: Callable[..., Any],
                  scan_id_of: Callable[[Path], Optional[int]]) -> Optional[Quote]:
    """``--dry-run`` without a stored scan: structure-only scan into a throwaway state dir."""
    from ..core.paths import ProjectLayout

    tmp = Path(tempfile.mkdtemp(prefix="discover-dry-run-"))
    try:
        temp_layout = ProjectLayout.for_repo(layout.repo, state_dir=tmp / "state")
        q = quote_from_structure_scan(temp_layout, cfg, branch, run, scan_id_of)
        if q is not None:
            q.scan_id = None
        return q
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _structure(cfg):
    import copy

    c = copy.deepcopy(cfg)
    c.no_llm = True
    return c


def _prose(cfg):
    import copy

    c = copy.deepcopy(cfg)
    c.no_llm = False
    return c


# ── decision ─────────────────────────────────────────────────────────────────

def decide(quote: Optional[Quote], cfg, *, yes: bool, can_ask: bool,
           ask: Callable[[str, bool], bool] | None = None) -> str:
    """'approved' | 'auto' (under the ask threshold) | 'pre_approved' (--yes); raises on decline/no TTY."""
    if yes:
        return "pre_approved"
    usd = quote.estimate.expected_usd if quote is not None else None
    v = verdict(usd, cfg) if usd is not None else ASK_YES
    if v == RUN:
        return "auto"
    if not can_ask:
        what = f"estimated ${usd:.2f}" if usd is not None else "an unknown cost (no estimate)"
        raise DiscoveryError(f"--prose would spend {what}; confirming needs a terminal", "cost_gate_no_tty",
                             hint="pass --yes to approve the spend, or --no-prose for a structure-only scan")
    prompt = (f"Prose scan estimated ${usd:.2f} (range ${quote.estimate.low_usd:.2f}–"
              f"${quote.estimate.high_usd:.2f}). Continue?") if usd is not None \
        else "Prose scan cost could not be estimated. Continue?"
    try:
        ok = (ask or _confirm)(prompt, v != ASK_NO)
    except (EOFError, KeyboardInterrupt):
        ok = False
    if not ok:
        raise DiscoveryError("prose scan declined at the cost gate", "cost_gate_declined",
                             hint="--dry-run shows the estimate per stage; --no-prose scans without LLM calls")
    return "approved"


def _confirm(prompt: str, default: bool) -> bool:
    import typer

    try:
        return bool(typer.confirm(prompt, default=default, err=True))
    except typer.Abort:
        return False


# ── record (estimate vs actual) ──────────────────────────────────────────────

def record(db_path: Path, quote: Optional[Quote], decision: str, scan_id: Optional[int]) -> None:
    """One `cost_estimates` row: the quote, the decision and (when *scan_id* ran) the actual spend."""
    if quote is None or not db_path.exists():
        return
    from ..db import get_conn

    conn = get_conn(db_path)
    try:
        actual, actual_json = None, None
        if scan_id is not None and decision != "declined":
            rows = conn.execute("SELECT tier, model, calls, tokens_in, tokens_out, est_usd FROM llm_costs "
                                "WHERE scan_id = ?", (scan_id,)).fetchall()
            actual = round(sum((r[5] or 0) for r in rows), 6)
            actual_json = json.dumps([{"role": r[0], "model": r[1], "calls": r[2], "input_tokens": r[3],
                                       "output_tokens": r[4], "usd": r[5]} for r in rows])
        e = quote.estimate.as_dict()
        conn.execute(
            "INSERT INTO cost_estimates (scan_id, created_at, source, decision, expected_usd, low_usd, high_usd, "
            "stages_json, shape_json, actual_usd, actual_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (scan_id, datetime.now(timezone.utc).isoformat(timespec="seconds"), quote.source, decision,
             e["expected_usd"], e["low_usd"], e["high_usd"], json.dumps(e["stages"]),
             json.dumps(asdict(quote.shape)), actual, actual_json))
        conn.commit()
    finally:
        conn.close()


# ── rendering ────────────────────────────────────────────────────────────────

def render(console, quote: Optional[Quote], cfg) -> None:
    if quote is None:
        console.print("[yellow]no estimate: the structure scan produced no graph[/]")
        return
    e = quote.estimate
    src = "stored scan of HEAD" if quote.source == "index" else "structure-only scan (dry run)"
    s = quote.shape
    console.print(f"[bold]Prose cost estimate[/] (shape from {src}: {s.tier1_chunks} chunks, {s.domains} domains, "
                  f"{s.scenarios} scenarios, {s.screens} screens)", highlight=False)
    console.print(f"  {'stage':<14} {'role':<8} {'model':<22} {'calls':>5} {'expected':>9}  range", highlight=False)
    for st in e.stages:
        console.print(f"  {st.stage:<14} {st.role:<8} {st.model[:22]:<22} {st.calls:>5} {f'${st.usd:.2f}':>9}  "
                      f"${st.usd * RANGE_LOW:.2f}–${st.usd * RANGE_HIGH:.2f}", highlight=False)
    console.print(f"  {'total':<14} {'':<8} {'':<22} {'':>5} {f'${e.expected_usd:.2f}':>9}  "
                  f"${e.low_usd:.2f}–${e.high_usd:.2f}", highlight=False)
    ask, default_no = thresholds(cfg)
    v = verdict(e.expected_usd, cfg)
    action = {RUN: f"runs without asking (< ${ask:.2f})",
              ASK_YES: f"asks, default Yes (${ask:.2f}–${default_no:.2f})",
              ASK_NO: f"asks, default No (> ${default_no:.2f})"}[v]
    console.print(f"  cost gate: {action}", highlight=False)
    for note in e.notes:
        console.print(f"  [dim]· {note}[/]", highlight=False)
