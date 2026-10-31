"""Per-run metrics for the agent-loop eval (roadmap item M5.7)."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Iterable, Optional

from .mine import SOURCE_EXT, TEST_PATH

TEST_CMD = re.compile(r"\b(mvnw?\s+[^|;&]*\btest\b|gradlew?\s+[^|;&]*\btest\b|dotnet\s+test|pytest|npm\s+(run\s+)?test|"
                      r"yarn\s+test|jest|vitest|node\s+--test)\b")
BUILD_CMD = re.compile(r"\b(mvnw?\s+[^|;&]*\b(compile|package|verify)\b|gradlew?\s+[^|;&]*\b(build|compileJava)\b|"
                       r"dotnet\s+build|tsc\b|npm\s+run\s+build|node\s+--check)")


def file_scores(changed: Iterable[str], gold: Iterable[str], stack: str) -> dict:
    """Files-touched recall/precision on source files (tests excluded from both sides)."""
    exts = SOURCE_EXT[stack]
    touched = {f for f in changed if Path(f).suffix in exts and not TEST_PATH.search(f)}
    gold = set(gold)
    hit = touched & gold
    return {"touched": sorted(touched), "hit": sorted(hit), "missed": sorted(gold - touched),
            "extra": sorted(touched - gold),
            "recall": round(len(hit) / len(gold), 4) if gold else None,
            "precision": round(len(hit) / len(touched), 4) if touched else (None if not gold else 0.0)}


def parse_claude_stream(lines: Iterable[str]) -> dict:
    """Usage, tool calls and test/build activity from ``claude -p --output-format stream-json`` output."""
    tools: Counter = Counter()
    bash: list[str] = []
    result: dict = {}
    for line in lines:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "assistant":
            for block in (ev.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tools[block.get("name", "?")] += 1
                    if block.get("name") == "Bash":
                        bash.append(str((block.get("input") or {}).get("command", "")))
        elif ev.get("type") == "result":
            result = ev
    usage = result.get("usage") or {}
    return {
        "finished": bool(result) and not result.get("is_error", False),
        "stop": result.get("subtype"),
        "output_tokens": usage.get("output_tokens"),
        "input_tokens": usage.get("input_tokens"),
        "cache_read_tokens": usage.get("cache_read_input_tokens"),
        "cost_usd": result.get("total_cost_usd"),
        "turns": result.get("num_turns"),
        "tool_calls": sum(tools.values()),
        "tool_calls_by_name": dict(tools),
        "discover_tool_calls": sum(n for k, n in tools.items() if k.startswith("mcp__ai-discovery__")),
        "skills_used": sorted({k for k in tools if k == "Skill"}),
        "ran_tests": any(TEST_CMD.search(c) for c in bash),
        "ran_build": any(BUILD_CMD.search(c) for c in bash),
    }


def parse_codex_stream(lines: Iterable[str]) -> dict:
    """Best-effort metrics from ``codex exec --json`` (event schema Unverified; missing fields stay None)."""
    tools = 0
    commands: list[str] = []
    out_tokens: Optional[int] = None
    finished = False
    for line in lines:
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        kind = str(ev.get("type", ""))
        item = ev.get("item") or {}
        if item.get("type") in ("command_execution", "mcp_tool_call", "function_call"):
            tools += 1
            if item.get("command"):
                commands.append(str(item["command"]))
        usage = ev.get("usage") or (ev.get("info") or {}).get("total_token_usage") or {}
        if usage.get("output_tokens") is not None:
            out_tokens = usage["output_tokens"]
        finished = finished or kind in ("turn.completed", "task_complete")
    return {"finished": finished, "output_tokens": out_tokens, "tool_calls": tools, "cost_usd": None,
            "ran_tests": any(TEST_CMD.search(c) for c in commands),
            "ran_build": any(BUILD_CMD.search(c) for c in commands)}
