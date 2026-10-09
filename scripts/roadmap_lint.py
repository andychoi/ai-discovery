#!/usr/bin/env python3
"""Validate docs/roadmap/milestones/*.md and list ready work items.

Item format (one per level-3 heading):

    ### M0.2 Golden characterization harness
    - **Status:** Planned | Exploring | In progress (<branch>, <date>) | Done (<commit>) | Blocked (<reason>)
    - **Size:** S | M | L
    - **Lane:** A | B | C | D | E | F | G | Q
    - **Depends on:** M0.1, M1.2   (or: —)
    - **Owns:** ...
    - **Acceptance:** ...  (may continue on indented sub-bullets)
    - **Verify:** ...

Usage:
    python scripts/roadmap_lint.py              # validate, exit 1 on errors
    python scripts/roadmap_lint.py --next       # list ready items (Planned, all deps Done)
    python scripts/roadmap_lint.py --next --lane C
    python scripts/roadmap_lint.py --board      # status summary per milestone
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MILESTONES = ROOT / "docs" / "roadmap" / "milestones"

ITEM_RE = re.compile(r"^### (M\d+\.\d+(?:\.[a-z])?)\s+(.*)$")
FIELD_RE = re.compile(r"^- \*\*([A-Za-z ]+):\*\*\s*(.*)$")
ID_RE = re.compile(r"M\d+\.\d+(?:\.[a-z])?")
REQUIRED = ("Status", "Size", "Lane", "Depends on", "Owns", "Acceptance", "Verify")
STATUS_RE = re.compile(r"^(Planned|Exploring|In progress \(.+\)|Done(?: \(.+\))?|Blocked \(.+\))$")
LANES = set("ABCDEFGQ")
SIZES = {"S", "M", "L"}


def parse(paths: list[Path]) -> tuple[dict[str, dict], list[str]]:
    items: dict[str, dict] = {}
    errors: list[str] = []
    for path in paths:
        current = None
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            m = ITEM_RE.match(line)
            if m:
                item_id, title = m.groups()
                if item_id in items:
                    errors.append(f"{path.name}:{lineno}: duplicate item {item_id}")
                current = {"id": item_id, "title": title.strip(), "file": path.name, "line": lineno}
                items[item_id] = current
                continue
            if line.startswith("## ") or line.startswith("### "):
                current = None
                continue
            if current is not None:
                f = FIELD_RE.match(line)
                if f and f.group(1) not in current:
                    current[f.group(1)] = f.group(2).strip()
    return items, errors


def validate(items: dict[str, dict]) -> list[str]:
    errors = []
    for it in items.values():
        loc = f"{it['file']}:{it['line']} {it['id']}"
        for field in REQUIRED:
            if field not in it:
                errors.append(f"{loc}: missing field '{field}'")
        status = it.get("Status", "")
        if status and not STATUS_RE.match(status):
            errors.append(f"{loc}: bad Status '{status}'")
        if it.get("Size") and it["Size"] not in SIZES:
            errors.append(f"{loc}: bad Size '{it['Size']}'")
        if it.get("Lane") and it["Lane"] not in LANES:
            errors.append(f"{loc}: bad Lane '{it['Lane']}'")
        for dep in deps_of(it):
            if dep not in items:
                errors.append(f"{loc}: unknown dependency {dep}")
            elif dep == it["id"]:
                errors.append(f"{loc}: depends on itself")
    return errors


def deps_of(it: dict) -> list[str]:
    return ID_RE.findall(it.get("Depends on", ""))


def is_done(it: dict) -> bool:
    return it.get("Status", "").startswith("Done")


def ready(items: dict[str, dict], lane: str | None) -> list[dict]:
    out = []
    for it in items.values():
        if it.get("Status") != "Planned":
            continue
        if lane and it.get("Lane") != lane:
            continue
        if all(is_done(items[d]) for d in deps_of(it) if d in items):
            out.append(it)
    # ★ (first-lovable-slice) items first, then by ID.
    return sorted(out, key=lambda i: ("★" not in i["title"], [int(x) if x.isdigit() else x for x in re.split(r"[.M]", i["id"]) if x]))


def board(items: dict[str, dict]) -> None:
    by_ms: dict[str, dict[str, int]] = {}
    for it in items.values():
        ms = it["id"].split(".")[0]
        state = it.get("Status", "?").split(" ")[0]
        if state == "In":
            state = "In progress"
        by_ms.setdefault(ms, {}).setdefault(state, 0)
        by_ms[ms][state] += 1
    for ms in sorted(by_ms, key=lambda s: int(s[1:])):
        counts = ", ".join(f"{k}: {v}" for k, v in sorted(by_ms[ms].items()))
        print(f"{ms:4} {counts}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--next", action="store_true", help="list items ready to start")
    ap.add_argument("--lane", help="filter --next by lane letter")
    ap.add_argument("--board", action="store_true", help="status counts per milestone")
    args = ap.parse_args()

    paths = sorted(MILESTONES.glob("M*.md"))
    if not paths:
        print(f"no milestone files in {MILESTONES}", file=sys.stderr)
        return 1
    items, errors = parse(paths)
    errors += validate(items)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    if args.board:
        board(items)
    if args.next:
        for it in ready(items, args.lane):
            print(f"{it['id']:8} [{it['Lane']}] {it['title']}  ({it['file']})")
    if not (args.board or args.next):
        print(f"ok: {len(items)} items in {len(paths)} milestone files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
