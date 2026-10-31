"""The ``Answer`` envelope every query returns (design/mcp-and-agents.md §2).

One shape for MCP, the HTTP API and the CLI::

    {"status": "ok"|"not_found"|"not_indexed"|"unknown", "data": {...}, "totals": {...},
     "truncated": bool, "omitted": [{"field", "dropped", "total"}], "meta": {...}}

Honesty rules (docs/roadmap/README.md):

- an unresolved target is ``not_found`` (with ``did_you_mean``), never an empty ``ok``;
- an ambiguous target is ``unknown`` (with ``candidates``), never a guess;
- lists are ranked *then* cut, and every cut is recorded with the true total.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional, TypeVar

STATUSES = ("ok", "not_found", "not_indexed", "unknown")
TRI_STATES = ("yes", "no", "unknown")
CONTRACT_VERSION = "1"

T = TypeVar("T")


@dataclass
class Answer:
    status: str = "ok"
    data: dict = field(default_factory=dict)
    totals: dict = field(default_factory=dict)
    omitted: list[dict] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    remedy: Optional[str] = None

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"unknown answer status {self.status!r}")

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def truncated(self) -> bool:
        return bool(self.omitted)

    def cap(self, name: str, items: Iterable[T], limit: int, *, key: Optional[Callable[[T], Any]] = None) -> list[T]:
        """Rank *items* by *key* (if given), keep *limit*, record the true total and any cut."""
        ranked = sorted(items, key=key) if key is not None else list(items)
        total = len(ranked)
        self.totals[name] = total
        if total > limit:
            self.omitted.append({"field": name, "dropped": total - limit, "total": total})
        return ranked[:limit]

    def to_dict(self) -> dict:
        data = dict(self.data)
        if self.remedy and "remedy" not in data:
            data["remedy"] = self.remedy
        meta = dict(self.meta)
        meta["truncated"] = self.truncated
        meta["omitted_count"] = sum(o["dropped"] for o in self.omitted)
        return {"status": self.status, "data": data, "totals": dict(self.totals),
                "truncated": self.truncated, "omitted": list(self.omitted), "meta": meta}


def not_indexed(meta: Optional[dict] = None) -> Answer:
    return Answer(status="not_indexed", data={}, meta=meta or {"contract_version": CONTRACT_VERSION,
                                                                "scan_id": None, "degraded": []},
                  remedy="run `discover scan` in the repository first")


def not_found(target: str, did_you_mean: list[str], meta: dict, **extra: Any) -> Answer:
    return Answer(status="not_found", data={"target": target, "did_you_mean": did_you_mean, **extra}, meta=meta,
                  remedy="check the spelling or call `search` first" if did_you_mean else
                  "call `search` to find the right identifier")


def ambiguous(target: str, candidates: list[dict], meta: dict) -> Answer:
    return Answer(status="unknown", data={"target": target, "reason": "ambiguous", "candidates": candidates},
                  meta=meta, remedy="pass one of the candidates' ids (a qualified name or page id)")
