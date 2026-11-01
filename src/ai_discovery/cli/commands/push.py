"""`discover push --target dochub|gitea` — deliver the wiki's markdown export (roadmap item M2.6).

Replaces the legacy ``ingest`` (the old SDLC tree) and ``ingest-docs`` (removed). What is delivered is the
M3.10 markdown export, one document per page, refreshed under ``<state>/exports/markdown`` first so you can
see exactly what went out.

One vocabulary for both targets: URLs come from config (``push.dochub_url``, ``push.gitea_url``,
``push.gitea_owner``/``gitea_repo``), credentials only from the environment (``DISCOVERY_DOCHUB_TOKEN`` sent
as ``X-Api-Key``; ``DISCOVERY_GITEA_TOKEN``). Each page's last delivery per target is kept in ``page_push``
(migration 0007): ``--only-failed`` retries just the failures. ``--dry-run`` lists what would be sent and
touches nothing. Exit 0 when everything was delivered, 1 when any page failed, ``push_auth_failed`` (1) when
the target refuses the credentials.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import typer

from ...core.errors import DiscoveryError
from ..context import get_globals
from ..output import emit, handle_errors

TARGETS = ("dochub", "gitea")
TOKEN_ENV = {"dochub": "DISCOVERY_DOCHUB_TOKEN", "gitea": "DISCOVERY_GITEA_TOKEN"}
_ID_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


class AuthRefused(Exception):
    pass


def doc_id(slug: str, page_id: str) -> str:
    kind, _, key = page_id.partition(":")
    return f"{slug}-{kind}-{_ID_UNSAFE.sub('_', key).strip('_')}"[:200]


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── delivery ─────────────────────────────────────────────────────────────────

def deliver_dochub(client, base: str, slug: str, docs: list[dict], token: Optional[str],
                   batch_size: int) -> dict[str, tuple[str, str, str]]:
    """{page_id: (status, url, error)} via the DocHub batch ingest endpoint."""
    headers = {"X-Api-Key": token} if token else {}
    out: dict[str, tuple[str, str, str]] = {}
    by_doc = {doc_id(slug, d["page_id"]): d["page_id"] for d in docs}
    for i in range(0, len(docs), max(1, batch_size)):
        chunk = docs[i:i + batch_size]
        payload = {"docs": [{"doc_id": doc_id(slug, d["page_id"]), "doc_type": d["type"], "title": d["title"],
                             "body": d["text"], "domain": d["domain"], "confidence": d["confidence"],
                             "page_id": d["page_id"]} for d in chunk]}
        try:
            resp = client.post(f"{base.rstrip('/')}/api/projects/{slug}/docs/ingest/batch", json=payload,
                               headers=headers, timeout=120.0)
        except Exception as exc:   # noqa: BLE001 — network failure: every page in the chunk failed
            for d in chunk:
                out[d["page_id"]] = ("failed", "", f"{type(exc).__name__}: {exc}"[:300])
            continue
        if resp.status_code in (401, 403):
            raise AuthRefused(f"DocHub refused the credentials (HTTP {resp.status_code})")
        if resp.status_code >= 400:
            for d in chunk:
                out[d["page_id"]] = ("failed", "", f"HTTP {resp.status_code}: {resp.text[:200]}")
            continue
        data = resp.json() if resp.content else {}
        for r in data.get("results", []):
            pid = by_doc.get(r.get("doc_id"), r.get("page_id"))
            if pid:
                out[pid] = ("pushed", f"{base.rstrip('/')}{r.get('url', '')}", "")
        for e in data.get("errors", []):
            pid = by_doc.get(e.get("doc_id"), e.get("page_id"))
            if pid:
                out[pid] = ("failed", "", str(e.get("error", "rejected"))[:300])
        for d in chunk:   # a page the server neither accepted nor rejected did not arrive
            out.setdefault(d["page_id"], ("failed", "", "not acknowledged by DocHub"))
    return out


def deliver_gitea(client, base: str, owner: str, repo: str, slug: str, docs: list[dict],
                  token: Optional[str]) -> dict[str, tuple[str, str, str]]:
    """{page_id: (status, url, error)} via the Gitea contents API (create or update each file)."""
    headers = {"Authorization": f"token {token}"} if token else {}
    api = f"{base.rstrip('/')}/api/v1/repos/{owner}/{repo}/contents"
    out: dict[str, tuple[str, str, str]] = {}
    for d in docs:
        path = f"projects/{slug}/{d['path']}"
        try:
            existing = client.get(f"{api}/{path}", headers=headers, timeout=15.0)
            if existing.status_code in (401, 403):
                raise AuthRefused(f"Gitea refused the credentials (HTTP {existing.status_code})")
            body: dict[str, Any] = {"content": base64.b64encode(d["text"].encode("utf-8")).decode(),
                                    "message": f"discovery: update {d['page_id']}"}
            if existing.status_code == 200:
                body["sha"] = existing.json().get("sha")
            resp = client.put(f"{api}/{path}", json=body, headers=headers, timeout=30.0)
            if resp.status_code in (401, 403):
                raise AuthRefused(f"Gitea refused the credentials (HTTP {resp.status_code})")
            if resp.status_code >= 400:
                out[d["page_id"]] = ("failed", "", f"HTTP {resp.status_code}: {resp.text[:200]}")
                continue
            out[d["page_id"]] = ("pushed", (resp.json().get("content") or {}).get("html_url", ""), "")
        except AuthRefused:
            raise
        except Exception as exc:   # noqa: BLE001
            out[d["page_id"]] = ("failed", "", f"{type(exc).__name__}: {exc}"[:300])
    return out


# ── command ──────────────────────────────────────────────────────────────────

def run_push(layout, cfg, target: str, *, only_failed: bool = False, dry_run: bool = False,
             batch_size: Optional[int] = None, client=None) -> dict:
    from ...export import markdown as mexport
    from ...store import migrate

    push_cfg = getattr(cfg, "push", None)
    url = (getattr(push_cfg, "dochub_url" if target == "dochub" else "gitea_url", "") or "").strip()
    if not url and not dry_run:
        key = "push.dochub_url" if target == "dochub" else "push.gitea_url"
        raise DiscoveryError(f"no {target} URL configured", "config_invalid_value",
                             hint=f"set `{key}` in .discovery/config.yaml")
    if not layout.db_path.exists():
        raise DiscoveryError("this repository has not been scanned", "not_indexed", hint="run `discover scan`")
    conn = sqlite3.connect(layout.db_path, timeout=30)
    try:
        migrate(conn)
        row = conn.execute("SELECT scan_id FROM current_scan WHERE id = 1").fetchone()
        if row is None:
            raise DiscoveryError("no completed scan to push", "not_indexed", hint="run `discover scan`")
        scan_id = row[0]
        commit = (conn.execute("SELECT commit_sha FROM scan_runs WHERE id = ?", (scan_id,)).fetchone() or [""])[0] or ""
        export_dir = layout.exports_dir / "markdown"
        mexport.write(conn, scan_id, export_dir, commit=commit)
        docs = mexport.pages(conn, scan_id, commit)
        if only_failed:
            failed = {r[0] for r in conn.execute("SELECT page_id FROM page_push WHERE target = ? AND status = 'failed'",
                                                 (target,))}
            docs = [d for d in docs if d["page_id"] in failed]
        result: dict[str, Any] = {"target": target, "scan_id": scan_id, "export": str(export_dir),
                                  "selected": len(docs), "only_failed": only_failed, "dry_run": dry_run}
        if dry_run or not docs:
            result.update(pushed=0, failed=0, sample=[d["page_id"] for d in docs[:20]])
            return result
        token = os.environ.get(TOKEN_ENV[target]) or None
        own_client = client is None
        if own_client:
            import httpx

            client = httpx.Client(follow_redirects=True)
        try:
            if target == "dochub":
                outcome = deliver_dochub(client, url, layout.slug, docs, token,
                                         batch_size or int(getattr(push_cfg, "batch_size", 50) or 50))
            else:
                outcome = deliver_gitea(client, url, getattr(push_cfg, "gitea_owner", "ai-docs"),
                                        getattr(push_cfg, "gitea_repo", "ai-docs-content"), layout.slug, docs, token)
        except AuthRefused as exc:
            raise DiscoveryError(str(exc), "push_auth_failed",
                                 hint=f"set {TOKEN_ENV[target]} to a valid token") from exc
        finally:
            if own_client:
                client.close()
        now = _now()
        sha = {d["page_id"]: _sha(d["text"]) for d in docs}
        conn.executemany(
            "INSERT OR REPLACE INTO page_push (page_id, target, status, url, error, content_sha, scan_id, pushed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(pid, target, st, u, err or None, sha.get(pid), scan_id, now) for pid, (st, u, err) in sorted(outcome.items())])
        conn.commit()
        failures = [{"page_id": pid, "error": err} for pid, (st, _u, err) in sorted(outcome.items()) if st == "failed"]
        result.update(pushed=sum(1 for st, _u, _e in outcome.values() if st == "pushed"), failed=len(failures),
                      failures=failures[:20])
        return result
    finally:
        conn.close()


def register(app: typer.Typer) -> None:
    @app.command("push")
    @handle_errors
    def push(
        ctx: typer.Context,
        target: str = typer.Option(..., "--target", help="dochub | gitea"),
        only_failed: bool = typer.Option(False, "--only-failed", help="Retry only pages whose last delivery failed."),
        dry_run: bool = typer.Option(False, "--dry-run", help="List what would be sent; send nothing."),
        batch_size: Optional[int] = typer.Option(None, "--batch-size", min=1, help="Pages per DocHub request."),
    ) -> None:
        """Deliver the wiki (markdown export, one document per page) to DocHub or Gitea."""
        from ...config.loader import load_config

        g = get_globals(ctx)
        if target not in TARGETS:
            raise DiscoveryError(f"--target must be one of {', '.join(TARGETS)}", "contradictory_flags")
        layout = g.layout()
        cfg = load_config(cwd=layout.repo)
        result = run_push(layout, cfg, target, only_failed=only_failed, dry_run=dry_run, batch_size=batch_size)

        def human(console) -> None:
            if dry_run:
                console.print(f"dry run: {result['selected']} page(s) would go to {target} "
                              f"(export in {result['export']})", highlight=False)
                for pid in result.get("sample", []):
                    console.print(f"  {pid}", highlight=False)
                return
            if not result["selected"]:
                console.print("nothing to push" + (" (no failed pages)" if only_failed else ""), highlight=False)
                return
            mark = "[green]✓[/]" if not result["failed"] else "[yellow]![/]"
            console.print(f"{mark} {result['pushed']} pushed to {target}, {result['failed']} failed", highlight=False)
            for f in result.get("failures", []):
                console.print(f"  [red]✗[/] {f['page_id']}: {f['error']}", highlight=False)
            if result["failed"]:
                console.print("Retry with [bold]discover push --target " + target + " --only-failed[/]")
        emit(g, result, human)
        if result.get("failed"):
            raise typer.Exit(code=1)
