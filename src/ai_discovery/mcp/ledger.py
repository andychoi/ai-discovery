"""Local tool-use ledger (roadmap item M5.13).

One JSON line per MCP tool call in ``<state>/logs/mcp_calls.jsonl``: which tool,
which client, a hash of the arguments (never their values — they can hold code or
diffs), outcome, response size, cuts and timing. The file is rotated by size and
**never leaves the machine**; nothing in ai-discovery sends it anywhere.

Used by ``discover status --agent`` and by the agent-loop eval (M5.7) for
tool-call counts. Disable with ``DISCOVER_MCP_LEDGER=0``. Writing is best-effort:
a ledger failure never affects a tool call.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

log = logging.getLogger(__name__)

FILE_NAME = "mcp_calls.jsonl"
ENV_DISABLE = "DISCOVER_MCP_LEDGER"
MAX_BYTES = 5 * 1024 * 1024
KEEP = 3


def args_hash(args: Optional[dict]) -> str:
    canonical = json.dumps(args or {}, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def entry_for(tool: str, client: str, args: Optional[dict], answer: dict) -> dict:
    meta = answer.get("meta") or {}
    data = answer.get("data") or {}
    budget = meta.get("response_budget") or {}
    return {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "tool": tool, "client": client or "unknown",
        "args_sha": args_hash(args), "arg_keys": sorted((args or {}).keys()),
        "status": answer.get("status"), "reason": data.get("reason") if answer.get("status") != "ok" else None,
        "tokens": budget.get("used_tokens"), "max_tokens": budget.get("max_tokens"),
        "truncated": bool(answer.get("truncated")), "omitted": meta.get("omitted_count", 0),
        "timing_ms": meta.get("timing_ms"), "scan_id": meta.get("scan_id"), "behind_by": meta.get("behind_by"),
    }


class Ledger:
    def __init__(self, path: Path, *, max_bytes: int = MAX_BYTES, keep: int = KEEP):
        self.path = Path(path)
        self.max_bytes = max_bytes
        self.keep = keep
        self._lock = threading.Lock()

    @classmethod
    def for_state_dir(cls, logs_dir: Path) -> Optional["Ledger"]:
        if os.environ.get(ENV_DISABLE, "1").strip().lower() in ("0", "false", "no", "off"):
            return None
        return cls(Path(logs_dir) / FILE_NAME)

    def _rotate(self) -> None:
        for i in range(self.keep - 1, 0, -1):
            src = self.path.with_name(f"{self.path.name}.{i}")
            if src.exists():
                src.replace(self.path.with_name(f"{self.path.name}.{i + 1}"))
        self.path.replace(self.path.with_name(f"{self.path.name}.1"))
        oldest = self.path.with_name(f"{self.path.name}.{self.keep + 1}")
        if oldest.exists():
            oldest.unlink()

    def record(self, entry: dict) -> None:
        try:
            line = json.dumps(entry, separators=(",", ":"), default=str) + "\n"
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if self.path.exists() and self.path.stat().st_size + len(line) > self.max_bytes:
                    self._rotate()
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(line)
        except OSError as exc:  # never let bookkeeping break a tool call
            log.debug("ledger write failed: %s", exc)


def read_entries(path: Path) -> Iterable[dict]:
    """Entries oldest first, across rotated files; malformed lines are skipped."""
    path = Path(path)
    rotated = []
    for p in path.parent.glob(f"{path.name}.*"):
        suffix = p.name[len(path.name) + 1:]
        if suffix.isdigit():
            rotated.append((int(suffix), p))
    files = [p for _, p in sorted(rotated, reverse=True)] + ([path] if path.exists() else [])
    for f in files:
        try:
            with f.open(encoding="utf-8") as fh:
                for line in fh:
                    try:
                        yield json.loads(line)
                    except ValueError:
                        continue
        except OSError:
            continue


def _pct(values: list[float], q: float) -> Optional[float]:
    if not values:
        return None
    values = sorted(values)
    return round(values[min(len(values) - 1, int(q * (len(values) - 1) + 0.5))], 1)


def summarize(path: Path) -> dict[str, Any]:
    """Per-tool counts, outcome rates, truncation and latency; ``calls == 0`` when no ledger exists."""
    by_tool: dict[str, list[dict]] = defaultdict(list)
    clients: Counter = Counter()
    first = last = None
    for e in read_entries(path):
        by_tool[e.get("tool") or "?"].append(e)
        clients[e.get("client") or "unknown"] += 1
        first = first or e.get("ts")
        last = e.get("ts") or last
    tools = {}
    for name, rows in sorted(by_tool.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        status = Counter(r.get("status") for r in rows)
        timings = [r["timing_ms"] for r in rows if isinstance(r.get("timing_ms"), (int, float))]
        tokens = [r["tokens"] for r in rows if isinstance(r.get("tokens"), int)]
        tools[name] = {
            "calls": len(rows), "ok": status.get("ok", 0), "not_found": status.get("not_found", 0),
            "unknown": status.get("unknown", 0), "not_indexed": status.get("not_indexed", 0),
            "truncated": sum(1 for r in rows if r.get("truncated")),
            "p50_ms": _pct(timings, 0.5), "p95_ms": _pct(timings, 0.95),
            "avg_tokens": round(sum(tokens) / len(tokens)) if tokens else None,
            "repeat_calls": len(rows) - len({r.get("args_sha") for r in rows}),
        }
    calls = sum(t["calls"] for t in tools.values())
    return {"path": str(path), "calls": calls, "first": first, "last": last, "clients": dict(clients),
            "tools": tools,
            "rates": {k: round(sum(t[k] for t in tools.values()) / calls, 3) if calls else None
                      for k in ("not_found", "unknown", "truncated")}}
