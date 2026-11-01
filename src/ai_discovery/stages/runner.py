"""StageRunner: ordered stage execution with fingerprints, ``--only`` and run records (M1.8, ADR-0003).

For each stage the runner computes a **fingerprint** — sha256 of the stage
version, the config keys the stage consumes, the scanned commit and the
upstream stage's fingerprint (so a change propagates downstream). Then:

* ``--only`` given and the stage is not named → if this scan has stored output
  for it, its gated phases are *suppressed* (reloaded via the stage's resume
  branch); otherwise it runs. Named stages are *forced*. Ungated stages always
  run (they are cheap or have no stored form).
* a ``complete`` ``stage_runs`` row for this scan with the same fingerprint →
  suppressed (reuse); a different fingerprint → *forced* (re-run even when the
  old phase checkpoint says done);
* otherwise the stage's own phase gate decides (first run, resume).

Every executed stage gets a ``stage_runs`` row (``running`` → ``complete`` /
``failed``); a reused stage keeps its ``complete`` row. Phase numbers stay only
as checkpoint-row aliases (``phase_checkpoints``). At run end the scan's
degradations are summarised (LLM truncations are recorded here).
"""

from __future__ import annotations

import hashlib
import importlib
import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Optional

from .context import RunContext


@dataclass(frozen=True)
class StageSpec:
    name: str
    phases: tuple[float, ...] = ()        # phases gated by ctx.phase_runs (reloadable)
    config_keys: tuple[str, ...] = ()     # "a.b" attribute paths or "model:<tier>"
    version: str = "1"


# Default order. `resolve` runs before the runner (it creates the scan row);
# `domains` runs inside `graph` (one phase-7 checkpoint).
STAGES: tuple[StageSpec, ...] = (
    StageSpec("screens", (2,), ("no_llm", "quality", "provider", "model:screen")),
    StageSpec("walk", (5,)),
    StageSpec("parse", (6,)),
    StageSpec("graph", (7,), version="2"),     # M1.12 ADR-0004 resolver
    StageSpec("slices", (8,), version="3"),    # M1.12.b following rules; M6.12 entry-point scoring
    StageSpec("sql"),
    StageSpec("entities"),
    StageSpec("embed", (9, 10), ("no_llm", "provider", "rag.enabled", "rag.chunk_size",
                                  "rag.chunk_overlap", "rag.skip_tests")),
    StageSpec("summaries", (11,), ("no_llm", "provider", "model:fast", "semantic_batching")),
    StageSpec("flows", (12, 13), ("no_llm", "quality", "provider", "model:standard")),
    StageSpec("rollup", (), ("no_llm", "quality", "provider", "model:deep")),
    StageSpec("visuals", (), ("no_llm", "process_mining.enabled")),
    StageSpec("review", (), ("no_llm", "quality", "provider", "model:fast")),
    StageSpec("render"),
    StageSpec("finalise"),
)
ALIASES = {"domains": "graph", "chunk": "embed", "rag_embed": "embed"}


def stage_names() -> list[str]:
    return [s.name for s in STAGES]


def selectable() -> list[str]:
    """Stages ``--only`` can select (the others always run)."""
    return [s.name for s in STAGES if s.phases]


def normalize_only(names: list[str]) -> set[str]:
    """Stage names (or legacy phase names/numbers) → canonical stage names; ValueError if unknown."""
    from ..pipeline import _PHASE_NAME_TO_NUM

    phase_to_stage = {p: s.name for s in STAGES for p in s.phases}
    out: set[str] = set()
    for raw in (n.strip() for n in names if n.strip()):
        key = ALIASES.get(raw, raw)
        if key in selectable():
            out.add(key)
            continue
        num = _PHASE_NAME_TO_NUM.get(raw) or (float(raw) if raw.replace(".", "", 1).isdigit() else None)
        if num in phase_to_stage:
            out.add(phase_to_stage[num])
            continue
        raise ValueError(f"{raw!r} is not a selectable stage (choose from: {', '.join(selectable())})")
    return out


def _config_value(config: Any, key: str) -> Any:
    if key.startswith("model:"):
        try:
            return config.get_model(key.split(":", 1)[1])
        except (KeyError, ValueError, AttributeError):
            return None
    value = config
    for part in key.split("."):
        value = getattr(value, part, None)
    return value


def fingerprint(spec: StageSpec, ctx: RunContext, upstream: str) -> str:
    consumed = {k: _config_value(ctx.config, k) for k in spec.config_keys}
    payload = json.dumps({"stage": spec.name, "version": spec.version, "config": consumed,
                          "commit": getattr(ctx.resolved, "commit_sha", None), "upstream": upstream},
                         sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


class StageRunner:
    def __init__(self, ctx: RunContext, stages: tuple[StageSpec, ...] = STAGES):
        self.ctx = ctx
        self.stages = stages
        self.decisions: list[tuple[str, str]] = []   # (stage, run|reuse|forced|skip)

    # ── stage_runs rows ────────────────────────────────────────────────────
    def _stored(self) -> dict[str, tuple[str, str]]:
        from ..db import get_conn

        conn = get_conn(self.ctx.db_path)
        try:
            rows = conn.execute("SELECT stage, fingerprint, status FROM stage_runs WHERE scan_id = ?",
                                (self.ctx.scan_id,)).fetchall()
        except sqlite3.OperationalError:   # pre-0002 DB
            return {}
        finally:
            conn.close()
        return {r[0]: (r[1], r[2]) for r in rows}

    def _record(self, spec: StageSpec, fp: str, status: str, *, start: bool = False) -> None:
        from ..db import get_conn, now_iso

        conn = get_conn(self.ctx.db_path)
        try:
            if start:
                conn.execute("INSERT OR REPLACE INTO stage_runs (scan_id, stage, version, fingerprint, status, "
                             "started_at, ended_at) VALUES (?, ?, ?, ?, ?, ?, NULL)",
                             (self.ctx.scan_id, spec.name, spec.version, fp, status, now_iso()))
            else:
                conn.execute("UPDATE stage_runs SET status = ?, ended_at = ? WHERE scan_id = ? AND stage = ?",
                             (status, now_iso(), self.ctx.scan_id, spec.name))
            conn.commit()
        except sqlite3.OperationalError:   # pre-0002 DB: run without records
            pass
        finally:
            conn.close()

    # ── execution ──────────────────────────────────────────────────────────
    def decide(self, spec: StageSpec, fp: str, stored: dict[str, tuple[str, str]]) -> str:
        """``run`` | ``reuse`` (reload stored output) | ``forced`` (re-run despite old checkpoints).

        Reuse needs a ``complete`` row for *this* scan: a new scan has nothing
        stored, so ``--only`` on it still runs every stage (before M1.8 the
        skip-list form of ``--only`` "reloaded" an empty scan and produced
        0 files — fixed here).
        """
        only = self.ctx.only
        prev = stored.get(spec.name)
        has_output = bool(spec.phases and prev and prev[1] == "complete")
        if only is not None:
            if spec.name in only:
                return "forced"
            return "reuse" if has_output else "run"
        if has_output:
            return "reuse" if prev[0] == fp else "forced"
        return "run"

    def run(self) -> Optional[str]:
        ctx = self.ctx
        stored = self._stored()
        upstream = ""
        for spec in self.stages:
            fp = fingerprint(spec, ctx, upstream)
            upstream = fp
            decision = self.decide(spec, fp, stored)
            self.decisions.append((spec.name, decision))
            if decision == "reuse":
                ctx.suppressed_phases.update(spec.phases)
            elif decision == "forced":
                ctx.forced_phases.update(spec.phases)
            module = importlib.import_module(f"{__package__}.{spec.name}")
            if decision != "reuse":
                self._record(spec, fp, "running", start=True)
            try:
                module.execute(ctx)
            except BaseException:
                if decision != "reuse":
                    self._record(spec, fp, "failed")
                raise
            if decision != "reuse":
                self._record(spec, fp, "complete")
            if ctx.halted:
                return ctx.result
        self._summarise()
        return ctx.final_status

    def _summarise(self) -> None:
        """Record LLM truncations, then print the scan's degradations."""
        ctx = self.ctx
        client = ctx.llm_client
        counts = client.truncation_counts() if client is not None and hasattr(client, "truncation_counts") else {}
        if counts:
            ctx.degrade("llm", "llm_truncated", sum(counts.values()), sorted(counts))
        stats = client.cache_stats() if client is not None and hasattr(client, "cache_stats") else {}
        if stats.get("hits"):
            from ..pipeline import console as _console

            _console.print(f"[dim]LLM response cache: {stats['hits']} answer(s) reused at $0, "
                           f"{stats['writes']} stored[/]")
        from ..db import get_conn
        from ..pipeline import console

        conn = get_conn(ctx.db_path)
        try:
            rows = conn.execute("SELECT stage, kind, count FROM degradations WHERE scan_id = ? ORDER BY stage, kind",
                                (ctx.scan_id,)).fetchall()
        except sqlite3.OperationalError:
            rows = []
        finally:
            conn.close()
        if rows:
            console.print("[yellow]Degradations:[/] " + ", ".join(f"{r[0]}/{r[1]} ×{r[2]}" for r in rows))
