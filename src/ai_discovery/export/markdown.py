"""Markdown export: one file per page with front matter (roadmap item M3.10; the input of ``push``)."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
from pathlib import Path

_UNSAFE = re.compile(r"[^A-Za-z0-9._@-]+")


def file_name(page_id: str) -> str:
    kind, _, key = page_id.partition(":")
    return f"{kind}/{_UNSAFE.sub('_', key).strip('_')[:150] or 'index'}.md"


def render(page_id: str, kind: str, title: str, body: str, confidence: float, freshness: str, hashes_json: str,
           commit: str = "") -> str:
    front = {"page_id": page_id, "type": kind, "title": title, "confidence": confidence,
             "freshness": freshness, "commit": commit, "source_hashes": json.loads(hashes_json or "{}")}
    lines = ["---"] + [f"{k}: {json.dumps(v, ensure_ascii=False, sort_keys=True)}" for k, v in front.items()]
    return "\n".join(lines) + f"\n---\n\n# {title}\n\n{body or ''}"


def pages(conn: sqlite3.Connection, scan_id: int, commit: str = "") -> list[dict]:
    """[{page_id, type, title, domain, confidence, path (export-relative), text}] in page-id order."""
    out, seen = [], set()
    for page_id, kind, title, body, confidence, freshness, hashes, facts in conn.execute(
            "SELECT page_id, type, title, body_md, confidence, freshness, source_hashes_json, facts_json FROM pages "
            "WHERE scan_id = ? ORDER BY page_id", (scan_id,)):
        name = file_name(page_id)
        if name in seen:                           # two keys sanitised alike: keep both, distinguishably
            name = name[:-3] + f"--{hashlib.sha1(page_id.encode()).hexdigest()[:8]}.md"
        seen.add(name)
        try:
            domain = (json.loads(facts or "{}").get("facts") or {}).get("domain") or ""
        except (ValueError, AttributeError):
            domain = ""
        out.append({"page_id": page_id, "type": kind, "title": title, "domain": str(domain or ""),
                    "confidence": confidence, "path": name,
                    "text": render(page_id, kind, title, body, confidence, freshness, hashes, commit)})
    return out


def write(conn: sqlite3.Connection, scan_id: int, out_dir: Path, *, commit: str = "") -> dict:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    items = pages(conn, scan_id, commit)
    for item in items:
        path = out_dir / item["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(item["text"], encoding="utf-8")
    return {"pages": len(items), "dir": str(out_dir)}
