"""Push generated docs to ai-docs platform (API or Gitea)."""

from __future__ import annotations

import base64
import logging
from pathlib import Path

import httpx

from ..db import get_conn

logger = logging.getLogger(__name__)


def push_docs(
    written_docs: list[dict],
    push_mode: str,
    project_slug: str,
    api_url: str | None = None,
    api_token: str | None = None,
    api_key: str | None = None,
    gitea_url: str | None = None,
    gitea_token: str | None = None,
    db_path: Path | None = None,
) -> list[dict]:
    """Push generated docs to ai-docs platform.

    Args:
        written_docs: list from write_docs(), each dict has: doc_id, doc_type,
            domain, file_path, confidence
        push_mode: "api" or "gitea"
        project_slug: target project
        api_url: DocHub base URL (required for api mode)
        api_token: Bearer token for API auth
        gitea_url: Gitea base URL (required for gitea mode)
        gitea_token: Gitea API token
        db_path: if provided, update generated_docs.push_status

    Returns:
        list of push results: [{doc_id, status, url, error}]
    """
    if push_mode == "api":
        return _push_api(written_docs, project_slug, api_url, api_token, api_key, db_path)
    elif push_mode == "gitea":
        return _push_gitea(written_docs, project_slug, gitea_url, gitea_token, db_path)
    else:
        raise ValueError(f"Invalid push_mode: {push_mode}")


def _push_api(
    docs: list[dict],
    project_slug: str,
    api_url: str | None,
    api_token: str | None,
    api_key: str | None,
    db_path: Path | None,
) -> list[dict]:
    """Push via DocHub REST API (batch ingest endpoint).

    Sends all docs in a single POST to /api/projects/{slug}/docs/ingest/batch.
    Falls back to per-doc requests if batch fails.

    Auth priority:
      1. X-Api-Key (static shared secret, preferred for service-to-service)
      2. Authorization: Bearer <jwt> (user token, legacy)
    """
    if not api_url:
        raise ValueError("--api-url required for api push mode")

    base = api_url.rstrip("/")
    if api_key:
        headers = {"X-Api-Key": api_key}
    elif api_token:
        headers = {"Authorization": f"Bearer {api_token}"}
    else:
        headers = {}

    # Build batch payload
    batch_items = []
    for doc in docs:
        try:
            content = Path(doc["file_path"]).read_text()
            batch_items.append({
                "doc_id": doc["doc_id"],
                "doc_type": doc["doc_type"],
                "body": content,
                "domain": doc.get("domain", ""),
                "confidence": doc.get("confidence", 0.0),
            })
        except Exception as e:
            logger.error("Could not read file for %s: %s", doc["doc_id"], e)

    if not batch_items:
        return []

    # Try batch first
    try:
        resp = httpx.post(
            f"{base}/api/projects/{project_slug}/docs/ingest/batch",
            headers=headers,
            json={"docs": batch_items},
            timeout=120.0,
        )
        resp.raise_for_status()
        data = resp.json()
        results = []
        for r in data.get("results", []):
            url = f"{base}{r.get('url', '')}"
            _update_push_status(db_path, r["doc_id"], "pushed", url)
            results.append({"doc_id": r["doc_id"], "status": "pushed", "url": url, "error": None})
        for e in data.get("errors", []):
            _update_push_status(db_path, e["doc_id"], "failed", "")
            results.append({"doc_id": e["doc_id"], "status": "failed", "url": "", "error": e["error"]})
        return results
    except Exception as batch_err:
        logger.warning("Batch ingest failed (%s), falling back to per-doc requests", batch_err)

    # Per-doc fallback
    results = []
    for item in batch_items:
        try:
            resp = httpx.post(
                f"{base}/api/projects/{project_slug}/docs/ingest",
                headers=headers,
                json=item,
                timeout=30.0,
            )
            resp.raise_for_status()
            url = f"{base}{resp.json().get('url', '')}"
            _update_push_status(db_path, item["doc_id"], "pushed", url)
            results.append({"doc_id": item["doc_id"], "status": "pushed", "url": url, "error": None})
        except Exception as e:
            logger.error("API push failed for %s: %s", item["doc_id"], e)
            _update_push_status(db_path, item["doc_id"], "failed", "")
            results.append({"doc_id": item["doc_id"], "status": "failed", "url": "", "error": str(e)})

    return results


def _push_gitea(
    docs: list[dict],
    project_slug: str,
    gitea_url: str | None,
    gitea_token: str | None,
    db_path: Path | None,
) -> list[dict]:
    """Push via Gitea API (create/update files in content repo).

    For each doc:
    1. Read the markdown file
    2. PUT to {gitea_url}/api/v1/repos/{owner}/{repo}/contents/{path}
       - Content repo: ai-docs-content
       - Path: projects/{project_slug}/{doc_type}/{doc_id}.md
    3. Record result
    """
    if not gitea_url:
        raise ValueError("--gitea-url required for gitea push mode")

    results = []
    content_repo = "ai-docs-content"

    for doc in docs:
        try:
            content = Path(doc["file_path"]).read_text()
            file_path = f"projects/{project_slug}/{doc['doc_type']}/{doc['doc_id']}.md"
            b64_content = base64.b64encode(content.encode()).decode()

            # Check if file exists first (to get SHA for update)
            existing_sha = _get_file_sha(gitea_url, content_repo, file_path, gitea_token)

            body: dict = {
                "content": b64_content,
                "message": f"discovery: update {doc['doc_id']}",
            }
            if existing_sha:
                body["sha"] = existing_sha

            resp = httpx.put(
                f"{gitea_url.rstrip('/')}/api/v1/repos/ai-docs/{content_repo}/contents/{file_path}",
                headers={"Authorization": f"token {gitea_token}"} if gitea_token else {},
                json=body,
                timeout=30.0,
            )
            resp.raise_for_status()
            url = resp.json().get("content", {}).get("html_url", "")
            _update_push_status(db_path, doc["doc_id"], "pushed", url)
            results.append({"doc_id": doc["doc_id"], "status": "pushed", "url": url, "error": None})
        except Exception as e:
            logger.error("Gitea push failed for %s: %s", doc["doc_id"], e)
            _update_push_status(db_path, doc["doc_id"], "failed", "")
            results.append({"doc_id": doc["doc_id"], "status": "failed", "url": "", "error": str(e)})

    return results


def _get_file_sha(gitea_url: str, repo: str, path: str, token: str | None) -> str | None:
    """Check if file exists in Gitea repo, return SHA if it does."""
    try:
        resp = httpx.get(
            f"{gitea_url.rstrip('/')}/api/v1/repos/ai-docs/{repo}/contents/{path}",
            headers={"Authorization": f"token {token}"} if token else {},
            timeout=10.0,
        )
        if resp.status_code == 200:
            return resp.json().get("sha")
    except Exception:
        pass
    return None


def _update_push_status(db_path: Path | None, doc_id: str, status: str, url: str) -> None:
    """Update push_status and push_url in generated_docs table."""
    if db_path is None:
        return
    conn = get_conn(db_path)
    try:
        conn.execute(
            "UPDATE generated_docs SET push_status = ?, push_url = ? WHERE doc_id = ?",
            (status, url, doc_id),
        )
        conn.commit()
    finally:
        conn.close()
