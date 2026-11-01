"""Exports are views over the stored pages and graph (roadmap item M3.10, ADR-0005 §7).

- ``json``: the versioned knowledge-graph document (supersedes ``export-graph``), no source bodies.
- ``html``: the wiki SPA plus every answer it reads inlined as ``data.js`` — opens from ``file://``.
- ``markdown``: one file per page with front matter (what ``push`` will deliver).
"""
