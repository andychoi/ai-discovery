"""Tool-call pipeline: shield → handler → budget → meta (roadmap item M5.3).

- **shield** — nothing escapes as an MCP error. An agent that sees ``isError``
  early tends to abandon the server, so bad arguments and internal failures come
  back as a normal ``Answer`` with ``status: "unknown"``, a ``reason`` and a
  ``remedy``.
- **budget** — the answer is measured (≈ 4 chars/token). Over the tool's cap, list
  fields are cut from the tail (lists are already ranked) in the tool's declared
  shed order; each cut is recorded in ``omitted`` with the true total. Long
  strings are clipped last.
- **safety** (M5.12) — secrets masked, injection-shaped text flagged, untrusted fields
  listed (``mcp/safety.py``); fails closed.
- **meta** — ``_meta.tool``, ``_meta.response_budget`` and total ``timing_ms``.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

from jsonschema import Draft202012Validator

from ..query.answer import Answer
from ..query.session import Project
from .safety import apply_safety
from .tool import ToolSpec

log = logging.getLogger(__name__)

CHARS_PER_TOKEN = 4
MAX_STRING = 4000
_validators: dict[str, Draft202012Validator] = {}


def estimate_tokens(obj: Any) -> int:
    return len(json.dumps(obj, separators=(",", ":"), default=str)) // CHARS_PER_TOKEN


def _bad_arguments(spec: ToolSpec, errors: list[str]) -> dict:
    return Answer(status="unknown", data={"reason": "bad_arguments", "errors": errors[:5]},
                  remedy=f"check the `{spec.name}` input schema (see tools/list)").to_dict()


def _validate(spec: ToolSpec, args: dict) -> list[str]:
    v = _validators.get(spec.name)
    if v is None:
        v = _validators[spec.name] = Draft202012Validator(spec.input_schema)
    return [f"{'/'.join(str(p) for p in e.absolute_path) or '(root)'}: {e.message}"
            for e in sorted(v.iter_errors(args), key=lambda e: list(e.absolute_path))]


def _get(data: dict, path: str) -> tuple[Optional[dict], Optional[str]]:
    parts = path.split(".")
    cur: Any = data
    for p in parts[:-1]:
        cur = cur.get(p) if isinstance(cur, dict) else None
        if cur is None:
            return None, None
    return (cur, parts[-1]) if isinstance(cur, dict) else (None, None)


def _record(answer: dict, field: str, dropped: int, total: int) -> None:
    for o in answer["omitted"]:
        if o["field"] in (field, field.rsplit(".", 1)[-1]):
            o["dropped"] += dropped
            return
    answer["omitted"].append({"field": field, "dropped": dropped, "total": total})


def _clip_strings(obj: Any, limit: int) -> int:
    """Clip strings longer than *limit* in place; returns how many were clipped."""
    n = 0
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and len(v) > limit:
                obj[k] = v[: limit - 1] + "…"
                n += 1
            else:
                n += _clip_strings(v, limit)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, str) and len(v) > limit:
                obj[i] = v[: limit - 1] + "…"
                n += 1
            else:
                n += _clip_strings(v, limit)
    return n


def apply_budget(spec: ToolSpec, answer: dict, max_tokens: Optional[int] = None) -> dict:
    cap = max_tokens or spec.budget.max_tokens
    used = estimate_tokens(answer)
    if used > cap:
        data = answer.get("data") or {}
        for path in spec.budget.shed:
            parent, key = _get(data, path)
            if parent is None or not isinstance(parent.get(key), list):
                continue
            items = parent[key]
            before = len(items)
            prior = next((o["dropped"] for o in answer["omitted"]
                          if o["field"] in (path, key)), 0)
            # binary search for the longest prefix that fits (lists are ranked: keep the head)
            lo, hi = 0, before
            while lo < hi:
                mid = (lo + hi + 1) // 2
                parent[key] = items[:mid]
                if estimate_tokens(answer) <= cap:
                    lo = mid
                else:
                    hi = mid - 1
            parent[key] = items[:lo]
            if lo < before:
                _record(answer, path, before - lo, before + prior)
            if estimate_tokens(answer) <= cap:
                break
        if estimate_tokens(answer) > cap and _clip_strings(data, MAX_STRING):
            answer["meta"].setdefault("degraded", []).append("strings_clipped")
        if answer["omitted"]:
            answer["truncated"] = True
    answer["meta"]["truncated"] = bool(answer.get("truncated"))
    answer["meta"]["omitted_count"] = sum(o["dropped"] for o in answer.get("omitted", []))
    answer["meta"]["response_budget"] = {"max_tokens": cap, "used_tokens": estimate_tokens(answer)}
    if answer["meta"]["response_budget"]["used_tokens"] > cap:
        answer["meta"].setdefault("degraded", []).append("over_budget")
    return answer


def call_tool(spec: ToolSpec, project: Project, args: Optional[dict], *, max_tokens: Optional[int] = None,
              signer: Any = None) -> dict:
    """Run one tool call through the full pipeline. Never raises."""
    started = time.monotonic()
    args = dict(args or {})
    errors = _validate(spec, args)
    if errors:
        answer = _bad_arguments(spec, errors)
    else:
        try:
            if spec.needs_signer:
                from ..query.annotations import Signer

                answer = spec.handler(project, args, signer or Signer("agent", "unknown", None))
            else:
                answer = spec.handler(project, args)
        except Exception as exc:  # the shield: domain or internal failure → success-shaped answer
            log.exception("tool %s failed", spec.name)
            answer = Answer(status="unknown", data={"reason": "internal_error", "error": type(exc).__name__,
                                                    "detail": str(exc)[:300]},
                            remedy="retry once; if it persists run `discover doctor` and report the error").to_dict()
    answer.setdefault("meta", {})
    answer.setdefault("omitted", [])
    answer.setdefault("totals", {})
    answer.setdefault("truncated", False)
    try:
        answer = apply_safety(answer)   # M5.12: mask secrets, flag injection-shaped text
    except Exception as exc:  # fail closed: never serve repository text that was not checked
        log.exception("safety envelope failed for %s", spec.name)
        answer = Answer(status="unknown", data={"reason": "safety_check_failed", "error": type(exc).__name__},
                        remedy="retry; if it persists run `discover doctor` and report the error").to_dict()
        answer["meta"]["degraded"] = ["safety_check_failed"]
    answer = apply_budget(spec, answer, max_tokens)
    answer["meta"]["tool"] = spec.name
    answer["meta"]["timing_ms"] = round((time.monotonic() - started) * 1000, 1)
    return answer
