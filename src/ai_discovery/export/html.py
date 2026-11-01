"""Static HTML export (roadmap item M3.10, design/wiki-web.md §6).

Copies the export bundle (``server/static_export``: one classic script, relative paths — browsers refuse
module scripts from ``file://``) and writes ``data.js``: ``window.__DISCOVERY_DATA__`` with every answer the
wiki reads, recorded by calling the real API in-process — so the export shows exactly what ``discover serve``
would. Search runs in the browser over an inlined index; notes and reviews are read-only; source snippets are
not included. Nothing is fetched: the folder works from ``file://``, offline.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from urllib.parse import quote

BUNDLE = Path(__file__).resolve().parent.parent / "server" / "static_export"
DATA_VERSION = 1
SEARCH_TEXT_CHARS = 4000
_MD_NOISE = re.compile(r"[`*_#>|\[\]()]+")


class BundleMissing(RuntimeError):
    pass


def page_path(page_id: str) -> str:
    """The path the SPA requests for a page (JS ``encodeURIComponent``)."""
    return "/pages/" + quote(page_id, safe="-_.!~*'()")


def _page_ids(layout) -> list[str]:
    import sqlite3

    if not layout.db_path.exists():
        return []
    conn = sqlite3.connect(f"file:{layout.db_path}?mode=ro", uri=True)
    try:
        row = conn.execute("SELECT scan_id FROM current_scan WHERE id = 1").fetchone()
        if row is None:
            return []
        return [r[0] for r in conn.execute("SELECT page_id FROM pages WHERE scan_id = ? ORDER BY page_id", (row[0],))]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def build_data(layout) -> dict:
    from fastapi.testclient import TestClient

    from ..server.app import create_app

    app = create_app(layout, allow_remote=True)    # in-process only: no socket is opened
    answers: dict[str, dict] = {}
    ids = _page_ids(layout)
    with TestClient(app) as client:
        def get(path: str) -> dict:
            res = client.get("/api/v1" + path)
            res.raise_for_status()
            return res.json()

        for path in ("/status", "/tree", "/screens"):
            answers[path] = get(path)
        project = (answers["/status"].get("data") or {}).get("project") or {}
        for key in ("repo", "state_dir"):          # a shared export must not carry local paths
            if key in project:
                project[key] = None
        search = []
        for pid in ids:
            ans = get(page_path(pid))
            answers[page_path(pid)] = ans
            page = ans.get("data") or {}
            text = _MD_NOISE.sub(" ", page.get("body_md") or "")
            search.append({"page_id": pid, "title": page.get("title") or pid, "type": page.get("type") or "",
                           "text": " ".join(text.split())[:SEARCH_TEXT_CHARS]})
    return {"version": DATA_VERSION, "answers": answers, "search": search}


def write(layout, out_dir: Path, *, bundle: Path = BUNDLE) -> dict:
    if not (bundle / "index.html").is_file():
        raise BundleMissing(f"no export bundle at {bundle} (build it with scripts/build_web.py)")
    if out_dir.exists():
        shutil.rmtree(out_dir)
    shutil.copytree(bundle, out_dir)
    data = build_data(layout)
    payload = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("</", "<\\/")       # a page body can never close the script element
    (out_dir / "data.js").write_text(f"window.__DISCOVERY_DATA__ = {payload};\n", encoding="utf-8")
    return {"pages": len(data["search"]), "index": str(out_dir / "index.html")}
