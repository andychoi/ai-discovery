"""Walk a directory of markdown files, extract metadata, prepare for ingestion."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .classifier import classify_markdown
from .frontmatter import DOC_TYPE_PREFIXES, generate_frontmatter, make_doc_id, prepend_frontmatter

_H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)

# Directories to skip when walking
_SKIP_DIRS = frozenset({
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    ".tox", ".mypy_cache", ".pytest_cache", "dist", "build",
})


@dataclass
class IngestFile:
    """A markdown file prepared for ingestion."""
    path: Path
    content: str          # original content
    title: str
    domain: str           # inferred from parent directory
    doc_type: str         # auto-classified
    doc_id: str = ""      # assigned after classification
    body: str = ""        # content with frontmatter prepended
    links_to: list[str] = field(default_factory=list)
    req_ids: list[str] = field(default_factory=list)
    stale: bool = False
    stale_reason: str = ""
    duplicate_of: str | None = None


def walk_markdown_files(docs_dir: Path) -> list[Path]:
    """Recursively find all .md files under docs_dir."""
    results = []
    for p in sorted(docs_dir.rglob("*.md")):
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        if p.name.lower() in ("readme.md", "index.md", "changelog.md", "license.md"):
            continue
        results.append(p)
    return results


def extract_title(content: str, filename: str) -> str:
    """Extract title from first H1 heading, or slugify filename."""
    match = _H1_RE.search(content)
    if match:
        return match.group(1).strip()
    # Fallback: humanize filename
    stem = Path(filename).stem
    return stem.replace("-", " ").replace("_", " ").title()


def infer_domain(file_path: Path, base_dir: Path) -> str:
    """Infer domain tag from parent directory relative to base."""
    try:
        rel = file_path.relative_to(base_dir)
    except ValueError:
        rel = file_path
    parts = [p for p in rel.parent.parts if p not in _SKIP_DIRS]
    if parts:
        return parts[0].lower().replace(" ", "-")
    return "general"


def prepare_files(
    docs_dir: Path,
    default_type: str = "as-is",
    domain_from_dirs: bool = True,
    start_ids: dict[str, int] | None = None,
) -> list[IngestFile]:
    """Walk, classify, and assign IDs for all files.

    Note: frontmatter is NOT generated here — it's deferred to after
    reference extraction and dedup so that links_to, req_ids, and status
    can be populated correctly.

    Args:
        docs_dir: root directory containing markdown files
        default_type: fallback doc type when classifier is unsure
        domain_from_dirs: use parent directory name as domain tag
        start_ids: starting sequence numbers per doc type (e.g. {"as-is": 5})

    Returns:
        list of IngestFile objects (body not yet populated)
    """
    counters: dict[str, int] = {}
    for dt in DOC_TYPE_PREFIXES:
        counters[dt] = (start_ids or {}).get(dt, 1)

    md_files = walk_markdown_files(docs_dir)
    results: list[IngestFile] = []

    for fp in md_files:
        content = fp.read_text(errors="replace")
        if not content.strip():
            continue

        title = extract_title(content, fp.name)
        domain = infer_domain(fp, docs_dir) if domain_from_dirs else "general"
        doc_type = classify_markdown(content, fp.name, default=default_type)

        # Ensure counter exists for this doc_type
        if doc_type not in counters:
            counters[doc_type] = (start_ids or {}).get(doc_type, 1)

        doc_id = make_doc_id(doc_type, counters[doc_type])
        counters[doc_type] += 1

        results.append(IngestFile(
            path=fp,
            content=content,
            title=title,
            domain=domain,
            doc_type=doc_type,
            doc_id=doc_id,
        ))

    return results


def finalize_bodies(files: list[IngestFile], source_dir: str = "") -> None:
    """Generate frontmatter and prepend to content for all files.

    Called after reference extraction and dedup so that links_to, req_ids,
    and status reflect the full analysis.
    """
    for f in files:
        status = "Deprecated" if f.stale else "Draft"
        fm = generate_frontmatter(
            doc_id=f.doc_id,
            title=f.title,
            doc_type=f.doc_type,
            tags=[f.domain] if f.domain != "general" else [],
            source_dir=source_dir,
            links_to=f.links_to,
            req_ids=f.req_ids,
            status=status,
        )
        f.body = prepend_frontmatter(f.content, fm)


def batch_files(files: list[IngestFile], batch_size: int = 20) -> list[list[IngestFile]]:
    """Split files into batches for API ingestion."""
    return [files[i:i + batch_size] for i in range(0, len(files), batch_size)]
