"""Annotations: agent and human findings, notes and proposed decisions (roadmap item M5.10).

Rules:

- **Never facts.** Annotations live in their own table; nothing here touches
  pages, nodes or edges, and impact/context results are never derived from them.
- **Signed.** Every row records ``signer_kind`` (agent | human), the client name,
  a session id and the scan it was made against. Status changes record who made them.
- **Decisions need a human.** An agent can only *propose* a decision; accepting or
  rejecting it requires ``signer_kind='human'`` (wiki or API).
- **Anchored, not lost.** Rows survive rescans and are re-anchored on read by
  qualified name or page id (following rename tombstones); an anchor that no longer
  exists is reported ``orphaned: true`` — never deleted.
- **Bounded.** Text ≤ 4,000 chars, ≤ 10 evidence items; at most ``write_limit()``
  agent writes per session (``$DISCOVER_MCP_WRITE_LIMIT``, default 50).
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from .answer import Answer, ambiguous, not_found, not_indexed
from .session import Project, Reader, loads
from .views import _TARGET_PREFIXES, _resolve_symbol

KINDS = ("finding", "note", "decision")
FINDING_STATUSES = ("open", "acknowledged", "resolved", "false_positive")
DECISION_STATUSES = ("proposed", "accepted", "rejected")
CATEGORIES = ("bug", "risk", "question", "doc_gap", "graph_gap", "other")
MAX_TEXT = 4000
MAX_EVIDENCE = 10
MAX_EVIDENCE_ITEM = 500
WRITE_LIMIT_ENV = "DISCOVER_MCP_WRITE_LIMIT"
DEFAULT_WRITE_LIMIT = 50


@dataclass(frozen=True)
class Signer:
    kind: str = "agent"              # agent | human
    client: Optional[str] = None
    session_id: Optional[str] = None


def write_limit() -> int:
    raw = os.environ.get(WRITE_LIMIT_ENV, "")
    return int(raw) if raw.isdigit() else DEFAULT_WRITE_LIMIT


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bad(reason: str, remedy: str, **extra: Any) -> dict:
    return Answer(status="unknown", data={"reason": reason, **extra}, remedy=remedy).to_dict()


def _rw(project: Project) -> sqlite3.Connection:
    from ..db import get_conn

    return get_conn(project.layout.db_path)


def _anchor(r: Reader, target: str) -> tuple[str, Any]:
    """('ok', (page_id|None, anchor_qn|None)) | ('not_found', did_you_mean) | ('ambiguous', candidates)."""
    prefix, sep, rest = target.partition(":")
    if sep and prefix in _TARGET_PREFIXES and prefix != "symbol":
        if r.page_row(target) is None:
            return "not_found", r.did_you_mean(target, types=[prefix])
        return "ok", (target, None)
    key = rest if prefix == "symbol" and sep else target
    state, found = _resolve_symbol(r, key)
    if state == "missing":
        return "not_found", r.did_you_mean(key)
    if state == "ambiguous":
        return "ambiguous", found
    qn = found[0]["qualified_name"]
    page = f"symbol:{qn}"
    return "ok", (page if r.page_row(page) is not None else None, qn)


def _evidence(items: Optional[list]) -> tuple[Optional[list], Optional[str]]:
    out = []
    for e in items or []:
        if isinstance(e, str):
            e = {"note": e}
        if not isinstance(e, dict):
            return None, "evidence items must be strings or {path, line, note} objects"
        clean = {k: e[k] for k in ("path", "line", "note") if e.get(k) is not None}
        if len(json.dumps(clean)) > MAX_EVIDENCE_ITEM:
            return None, f"each evidence item must be under {MAX_EVIDENCE_ITEM} characters"
        out.append(clean)
    if len(out) > MAX_EVIDENCE:
        return None, f"at most {MAX_EVIDENCE} evidence items"
    return out, None


def _row_dict(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "kind": row["kind"], "target": row["target"], "page_id": row["page_id"],
            "anchor_qn": row["anchor_qn"], "category": row["category"], "text": row["text"],
            "evidence": loads(row["evidence_json"], []), "status": row["status"],
            "signer_kind": row["signer_kind"], "client": row["client"], "session_id": row["session_id"],
            "scan_id": row["scan_id"], "created_at": row["created_at"], "updated_at": row["updated_at"],
            "updated_by": row["updated_by"]}


def create(project: Project, *, kind: str, target: str, text: str, signer: Signer,
           category: Optional[str] = None, evidence: Optional[list] = None) -> dict:
    """Add a finding / note / proposed decision anchored to *target*."""
    if kind not in KINDS:
        return _bad("bad_kind", f"kind must be one of {', '.join(KINDS)}")
    text = (text or "").strip()
    if not text or len(text) > MAX_TEXT:
        return _bad("bad_text", f"text must be 1–{MAX_TEXT} characters")
    if category is not None and category not in CATEGORIES:
        return _bad("bad_category", f"category must be one of {', '.join(CATEGORIES)}")
    ev, err = _evidence(evidence)
    if err:
        return _bad("bad_evidence", err)
    status = {"finding": "open", "decision": "proposed", "note": None}[kind]
    if kind == "decision" and signer.kind == "human":
        status = "accepted"
    with project.read() as r:
        if not r.indexed:
            return not_indexed().to_dict()
        state, found = _anchor(r, (target or "").strip())
        meta = r.meta()
        if state == "not_found":
            return not_found(target, found, meta).to_dict()
        if state == "ambiguous":
            return ambiguous(target, found, meta).to_dict()
        page_id, qn = found
        scan_id = r.scan_id
    conn = _rw(project)
    try:
        if signer.kind == "agent" and signer.session_id:
            used = conn.execute("SELECT COUNT(*) FROM annotations WHERE session_id = ? AND signer_kind = 'agent'",
                                (signer.session_id,)).fetchone()[0]
            if used >= write_limit():
                return Answer(status="unknown", meta=meta,
                              data={"reason": "write_limit_reached", "limit": write_limit(), "used": used},
                              remedy=f"this session reached its write limit; summarise in your reply instead "
                                     f"(operators can raise ${WRITE_LIMIT_ENV})").to_dict()
        now = _now()
        cur = conn.execute(
            "INSERT INTO annotations (kind, target, page_id, anchor_qn, category, text, evidence_json, status, "
            "signer_kind, client, session_id, scan_id, created_at, updated_at, updated_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (kind, target, page_id, qn, category, text, json.dumps(ev), status, signer.kind, signer.client,
             signer.session_id, scan_id, now, now, signer.kind))
        conn.commit()
        row = conn.execute("SELECT * FROM annotations WHERE id = ?", (cur.lastrowid,)).fetchone()
    finally:
        conn.close()
    ans = Answer(data=_row_dict(row), meta=meta)
    ans.meta["content_trust"] = {**meta.get("content_trust", {}), "untrusted": ["text", "evidence[].note"]}
    return ans.to_dict()


def set_status(project: Project, annotation_id: int, status: str, *, signer: Signer) -> dict:
    """Change a finding's or decision's status. Accepting/rejecting a decision needs a human."""
    if not project.layout.db_path.exists():
        return not_indexed().to_dict()
    conn = _rw(project)
    try:
        try:
            row = conn.execute("SELECT * FROM annotations WHERE id = ?", (int(annotation_id),)).fetchone()
        except sqlite3.OperationalError:
            return not_indexed().to_dict()
        if row is None:
            return Answer(status="not_found", data={"target": str(annotation_id), "did_you_mean": []},
                          remedy="list annotations with get_annotations").to_dict()
        kind = row["kind"]
        allowed = FINDING_STATUSES if kind == "finding" else DECISION_STATUSES if kind == "decision" else ()
        if status not in allowed:
            return _bad("bad_status", f"a {kind} takes one of: {', '.join(allowed) or 'no status'}",
                        kind=kind, status=status)
        if kind == "decision" and status in ("accepted", "rejected") and signer.kind != "human":
            return _bad("human_required", "only a person can accept or reject a decision — ask the user to do it "
                                          "in the wiki (`discover serve`)", id=row["id"])
        conn.execute("UPDATE annotations SET status = ?, updated_at = ?, updated_by = ? WHERE id = ?",
                     (status, _now(), signer.kind, row["id"]))
        conn.commit()
        row = conn.execute("SELECT * FROM annotations WHERE id = ?", (row["id"],)).fetchone()
    finally:
        conn.close()
    return Answer(data=_row_dict(row)).to_dict()


def for_anchor(r: Reader, *, page_id: Optional[str] = None, qn: Optional[str] = None) -> list[dict]:
    """Annotations attached to a page id (incl. renamed predecessors) or a qualified name, oldest first."""
    if not r.has_table("annotations") or not (page_id or qn):
        return []
    ids: set[str] = set()
    if page_id:
        from ..pages.notes import _aliases_of, resolve

        try:
            ids = _aliases_of(r.conn, resolve(r.conn, page_id)) | {page_id}
        except (ValueError, sqlite3.OperationalError):
            ids = {page_id}
    clauses, args = [], []
    if ids:
        clauses.append(f"page_id IN ({','.join('?' * len(ids))})")
        args.extend(sorted(ids))
    if qn:
        clauses.append("anchor_qn = ?")
        args.append(qn)
    rows = r.conn.execute(f"SELECT * FROM annotations WHERE {' OR '.join(clauses)} ORDER BY created_at, id",
                          args).fetchall()
    return [_row_dict(x) for x in rows]


def _orphaned(r: Reader, row: dict) -> bool:
    if row["anchor_qn"]:
        return r.conn.execute("SELECT 1 FROM code_nodes WHERE scan_id = ? AND qualified_name = ? LIMIT 1",
                              (r.scan_id, row["anchor_qn"])).fetchone() is None
    if row["page_id"]:
        from ..pages.notes import resolve

        try:
            current = resolve(r.conn, row["page_id"])
        except (ValueError, sqlite3.OperationalError):
            current = row["page_id"]
        return r.page_row(current) is None
    return True


def list_annotations(project: Project, *, target: Optional[str] = None, kind: Optional[str] = None,
                     status: Optional[str] = None, limit: int = 50) -> dict:
    """Annotations, newest first; ``orphaned`` marks rows whose anchor no longer exists in the current scan."""
    limit = max(1, min(int(limit), 200))
    with project.read() as r:
        if not r.indexed:
            return not_indexed().to_dict()
        if not r.has_table("annotations"):
            ans = Answer(data={"annotations": []}, meta=r.meta())
            ans.totals["annotations"] = 0
            return ans.to_dict()
        if target:
            state, found = _anchor(r, target.strip())
            if state == "not_found":
                return not_found(target, found, r.meta()).to_dict()
            if state == "ambiguous":
                return ambiguous(target, found, r.meta()).to_dict()
            page_id, qn = found
            rows = for_anchor(r, page_id=page_id, qn=qn)
        else:
            rows = [_row_dict(x) for x in r.conn.execute("SELECT * FROM annotations ORDER BY created_at, id")]
        if kind:
            rows = [x for x in rows if x["kind"] == kind]
        if status:
            rows = [x for x in rows if x["status"] == status]
        for x in rows:
            x["orphaned"] = _orphaned(r, x)
        ans = Answer()
        items = ans.cap("annotations", list(reversed(rows)), limit)
        ans.data = {"annotations": items}
        ans.meta = r.meta(untrusted=["annotations[].text", "annotations[].evidence[].note"])
        return ans.to_dict()
