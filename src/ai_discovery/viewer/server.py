"""Read-only HTTP server that renders ``discover scan`` output for a browser.

Design: the Python side only does HTTP + Jinja for the shell. All content
rendering (markdown → HTML, mermaid, BPMN, DMN) happens client-side via CDN
libraries, so this file stays simple and templates stay dumb.

Security: every filesystem lookup is resolved against a known root and
rejected if the resolved path escapes that root.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import re
import urllib.parse
from dataclasses import asdict, is_dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .dashboard import DashboardData, build_summary

logger = logging.getLogger(__name__)

# Content-type overrides for file extensions mimetypes doesn't know natively.
_EXTRA_TYPES: dict[str, str] = {
    ".md": "text/markdown; charset=utf-8",
    ".mmd": "text/plain; charset=utf-8",
    ".bpmn": "application/xml; charset=utf-8",
    ".dmn": "application/xml; charset=utf-8",
    ".j2": "text/plain; charset=utf-8",
}

_DIAGRAM_EXT: dict[str, str] = {
    "mermaid": ".mmd",
    "bpmn": ".bpmn",
    "dmn": ".dmn",
}

_PKG_ROOT = Path(__file__).parent
_TEMPLATES_DIR = _PKG_ROOT / "templates"
_STATIC_DIR = _PKG_ROOT / "static"


# ---------------------------------------------------------------------------
# Server context — templates, config, resolved roots
# ---------------------------------------------------------------------------

class ViewerContext:
    """Shared state threaded onto each request handler instance."""

    def __init__(
        self,
        *,
        slug: str,
        output_dir: Path,
        docs_root: Path,
    ) -> None:
        self.slug = slug
        self.output_dir = output_dir.resolve()
        self.docs_root = docs_root.resolve()
        self.slug_output_root = (self.output_dir / slug).resolve()
        self.slug_docs_root = (self.docs_root / slug).resolve()
        self.env = Environment(
            loader=FileSystemLoader(str(_TEMPLATES_DIR)),
            autoescape=select_autoescape(["html"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def summary(self) -> DashboardData:
        # Rebuilt per-request so a re-run of `discover scan` is picked up
        # without restarting the viewer.
        return build_summary(self.output_dir, self.docs_root, self.slug)

    def db_path(self) -> Optional[Path]:
        """Resolve the discovery DB the same way build_summary does (handles
        both the output-<slug> and legacy <slug> layouts)."""
        from .dashboard import discover_artifacts

        db_artifact = next(
            (a for a in discover_artifacts(self.output_dir, self.slug) if a.key == "db"),
            None,
        )
        return db_artifact.path if db_artifact and db_artifact.exists else None

    def resolve_raw(self, tree: str, rel: str) -> Optional[Path]:
        """Resolve /raw/<tree>/<rel> to a filesystem path, or None if escape."""
        root = {"output": self.slug_output_root, "docs": self.slug_docs_root}.get(tree)
        if root is None:
            return None
        candidate = (root / rel).resolve()
        if not _is_within(candidate, root):
            return None
        if not candidate.is_file():
            return None
        return candidate


def _is_within(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------

# Path parts allowed in URLs: alphanumerics plus ._- . Rejecting "." and ".."
# explicitly gives us cheap early failure for path-traversal attempts — the
# resolve/relative_to check in resolve_raw is the real backstop.
_SAFE_SEGMENT = re.compile(r"^(?!\.{1,2}$)[A-Za-z0-9._-]+$")


class _ViewerHandler(BaseHTTPRequestHandler):
    # Populated by ``make_server`` as a class attribute so every request sees
    # the same ViewerContext without threading it through BaseHTTPRequestHandler.
    ctx: ViewerContext

    # Quieter default log line (the stdlib one is noisy and misformatted).
    def log_message(self, fmt: str, *args: Any) -> None:
        logger.info("%s - %s", self.address_string(), fmt % args)

    # ----- routing -----

    def do_GET(self) -> None:   # noqa: N802 — required by stdlib base class
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        try:
            if path == "/" or path == "":
                self._serve_dashboard()
            elif path == "/api/summary":
                self._serve_summary_json()
            elif path == "/api/search":
                query = urllib.parse.parse_qs(parsed.query).get("q", [""])[0]
                self._serve_search_json(query)
            elif path.startswith("/api/node/"):
                self._serve_node_json(urllib.parse.unquote(path[len("/api/node/"):]))
            elif path.startswith("/node/"):
                self._serve_node_page(urllib.parse.unquote(path[len("/node/"):]))
            elif path.startswith("/doc/"):
                self._serve_doc(path[len("/doc/"):])
            elif path.startswith("/diagram/"):
                self._serve_diagram(path[len("/diagram/"):])
            elif path.startswith("/raw/"):
                self._serve_raw(path[len("/raw/"):])
            elif path.startswith("/static/"):
                self._serve_static(path[len("/static/"):])
            else:
                self._send_error(HTTPStatus.NOT_FOUND, "Unknown route")
        except Exception as exc:  # noqa: BLE001 — last-resort guard
            logger.exception("Unhandled error for %s", path)
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    # ----- route handlers -----

    def _serve_dashboard(self) -> None:
        data = self.ctx.summary()
        tpl = self.ctx.env.get_template("dashboard.html")
        html = tpl.render(data=data, slug=self.ctx.slug)
        self._send_html(html)

    def _serve_summary_json(self) -> None:
        data = self.ctx.summary()
        self._send_json(_dashboard_as_dict(data))

    # ----- A-5: node drill-down + search -----

    def _serve_search_json(self, query: str) -> None:
        from .dashboard import search_nodes

        db = self.ctx.db_path()
        results = search_nodes(db, query) if db else []
        self._send_json({"query": query, "results": results})

    def _serve_node_json(self, qualified_name: str) -> None:
        from .dashboard import node_detail

        db = self.ctx.db_path()
        detail = node_detail(db, qualified_name) if db else None
        if detail is None:
            self._send_error(HTTPStatus.NOT_FOUND, f"Unknown node: {qualified_name}")
            return
        self._send_json(detail)

    def _serve_node_page(self, qualified_name: str) -> None:
        from .dashboard import node_detail

        db = self.ctx.db_path()
        detail = node_detail(db, qualified_name) if db else None
        if detail is None:
            self._send_error(HTTPStatus.NOT_FOUND, f"Unknown node: {qualified_name}")
            return
        tpl = self.ctx.env.get_template("node.html")
        self._send_html(tpl.render(slug=self.ctx.slug, node=detail))

    def _serve_doc(self, rel: str) -> None:
        # Expect "<BUCKET>/<file>.md". Validate each segment.
        parts = rel.split("/")
        if len(parts) != 2 or not all(_SAFE_SEGMENT.match(p) for p in parts):
            self._send_error(HTTPStatus.BAD_REQUEST, "Bad doc path")
            return
        bucket, name = parts
        path = (self.ctx.slug_docs_root / bucket / name).resolve()
        if not _is_within(path, self.ctx.slug_docs_root) or not path.is_file():
            self._send_error(HTTPStatus.NOT_FOUND, "Doc not found")
            return
        tpl = self.ctx.env.get_template("doc.html")
        html = tpl.render(
            slug=self.ctx.slug, bucket=bucket, name=name,
            raw_url=f"/raw/docs/{bucket}/{name}",
        )
        self._send_html(html)

    def _serve_diagram(self, rel: str) -> None:
        # Expect "<type>/<file>". type in {mermaid,bpmn,dmn}.
        parts = rel.split("/")
        if len(parts) != 2 or not all(_SAFE_SEGMENT.match(p) for p in parts):
            self._send_error(HTTPStatus.BAD_REQUEST, "Bad diagram path")
            return
        dtype, name = parts
        if dtype not in _DIAGRAM_EXT:
            self._send_error(HTTPStatus.NOT_FOUND, f"Unknown diagram type: {dtype}")
            return
        # Backbone sits at the slug root (entity_backbone.mmd); others are in
        # per-type directories (bpmn/, dmn/, mermaid/).
        candidates = [
            self.ctx.slug_output_root / dtype / name,
            self.ctx.slug_output_root / name,
        ]
        path = next((p.resolve() for p in candidates if p.is_file()), None)
        if path is None or not _is_within(path, self.ctx.slug_output_root):
            self._send_error(HTTPStatus.NOT_FOUND, "Diagram not found")
            return
        raw_rel = path.relative_to(self.ctx.slug_output_root).as_posix()
        tpl = self.ctx.env.get_template("diagram.html")
        html = tpl.render(
            slug=self.ctx.slug, diagram_type=dtype, name=name,
            raw_url=f"/raw/output/{raw_rel}",
        )
        self._send_html(html)

    def _serve_raw(self, rel: str) -> None:
        # First segment is the tree selector: "output" or "docs".
        if "/" not in rel:
            self._send_error(HTTPStatus.BAD_REQUEST, "Missing tree prefix")
            return
        tree, _, sub = rel.partition("/")
        # Reject any path component that isn't a safe segment. This covers
        # ".." traversal, leading/trailing slashes, and NULs.
        for part in sub.split("/"):
            if not _SAFE_SEGMENT.match(part):
                self._send_error(HTTPStatus.BAD_REQUEST, "Bad raw path")
                return
        path = self.ctx.resolve_raw(tree, sub)
        if path is None:
            self._send_error(HTTPStatus.NOT_FOUND, "Raw file not found")
            return
        self._send_file(path)

    def _serve_static(self, rel: str) -> None:
        if not _SAFE_SEGMENT.match(rel):
            self._send_error(HTTPStatus.BAD_REQUEST, "Bad static path")
            return
        path = (_STATIC_DIR / rel).resolve()
        if not _is_within(path, _STATIC_DIR) or not path.is_file():
            self._send_error(HTTPStatus.NOT_FOUND, "Static not found")
            return
        self._send_file(path)

    # ----- low-level responders -----

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, obj: Any) -> None:
        body = json.dumps(obj, default=_json_default, indent=2).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        ctype = _EXTRA_TYPES.get(path.suffix.lower()) or \
                mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_error(self, status: HTTPStatus, msg: str) -> None:
        body = msg.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def _json_default(obj: Any) -> Any:
    if isinstance(obj, Path):
        return str(obj)
    if is_dataclass(obj):
        return asdict(obj)
    raise TypeError(f"Not JSON-serialisable: {type(obj).__name__}")


def _dashboard_as_dict(data: DashboardData) -> dict[str, Any]:
    """Return a browser-friendly dict (no Path objects, doc_tree flattened)."""
    return {
        "slug": data.slug,
        "db_path": str(data.db_path) if data.db_path else None,
        "artifacts": [
            {**asdict(a), "path": str(a.path)} for a in data.artifacts
        ],
        "histogram": [asdict(b) for b in data.histogram],
        "quality": asdict(data.quality) if data.quality else None,
        "weakest_docs": [asdict(d) for d in data.weakest_docs],
        "doc_tree": {
            bucket: [asdict(e) for e in entries]
            for bucket, entries in data.doc_tree.items()
        },
    }


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------

def make_server(
    ctx: ViewerContext,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> ThreadingHTTPServer:
    handler_cls = type(
        "_BoundViewerHandler", (_ViewerHandler,), {"ctx": ctx},
    )
    return ThreadingHTTPServer((host, port), handler_cls)


__all__ = ["ViewerContext", "make_server"]
