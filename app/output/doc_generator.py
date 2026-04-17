"""Render ai-docs-compatible markdown files from RollupResults using Jinja2."""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from ..ai.rollup import RollupResult

logger = logging.getLogger(__name__)


_DOC_TYPE_PREFIXES: dict[str, str] = {
    "as-is": "ASIS",
    "as-is-detail": "ASD",
    "as-is-schema": "ASSC",
    "spec": "SPEC",
    "data-model": "DM",
    "interface": "IF",
    "integration": "INT",
    "design": "DES",
    "adr": "ADR",
    "security-design": "SD",
    "ux-design": "UXD",
    "brd": "BRD",
    "sla-nfr": "SLA",
    "gap-analysis": "GAP",
    "test-plan": "TP",
    "dev-log": "DEV",
    "data-migration": "MIG",
    "deployment": "DEP",
    "runbook": "RB",
    "pcr": "PCR",
    "agent-config": "AGT",
    "process-flow": "PF",
}


def _slugify(text: str) -> str:
    """Lowercase, collapse non-alphanumeric to hyphens, strip edge hyphens."""
    if not text:
        return ""
    return re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")


def _doc_type_prefix(doc_type: str) -> str:
    return _DOC_TYPE_PREFIXES.get(doc_type, doc_type.upper().replace("-", ""))


def _make_doc_id(project_slug: str, domain: str, doc_type: str) -> str:
    return _slugify(f"{project_slug}-{domain}-{doc_type}")


def _get_template_env() -> Environment:
    """Create Jinja2 environment pointing at templates/ dir."""
    template_dir = Path(__file__).parent / "templates"
    return Environment(loader=FileSystemLoader(str(template_dir)), keep_trailing_newline=True)


def render_doc(
    rollup: RollupResult,
    project_slug: str,
    repo_url: str = "",
    repo_commit: str = "",
    unverified_claims: int = 0,
    links_to: list[str] | None = None,
) -> tuple[str, str]:
    """Render a single RollupResult to markdown string.

    Returns (doc_id, markdown_content).
    doc_id format: f"{project_slug}-{domain}-{doc_type}" (slugified), matching
    the ID stored by persist_rollups so DB and file layouts stay aligned.
    """
    env = _get_template_env()
    template_name = f"{rollup.doc_type}.md.j2"
    template = env.get_template(template_name)

    doc_id = _make_doc_id(project_slug, rollup.domain, rollup.doc_type)

    scan_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    rendered = template.render(
        doc_id=doc_id,
        title=rollup.title,
        scan_date=scan_date,
        repo_url=repo_url,
        repo_commit=repo_commit,
        confidence=rollup.confidence,
        unverified_claims=unverified_claims,
        links_to=links_to or [],
        content=rollup.content_md,
    )

    return doc_id, rendered


def write_docs(
    rollups: list[RollupResult],
    output_dir: Path,
    project_slug: str,
    repo_url: str = "",
    repo_commit: str = "",
    domain_adjacency: dict[str, list[str]] | None = None,
) -> list[dict]:
    """Render and write all rollups to output_dir/{DOCHUB_PREFIX}/{doc_id}.md.

    Folder layout uses DocHub type prefixes (e.g. ASIS, ASD, SPEC) so the
    on-disk tree matches DocHub's convention. doc_id uses the slug form
    `{project_slug}-{domain}-{doc_type}` and is stable across runs.

    Returns list of dicts with: doc_id, doc_type, domain, file_path, confidence
    """
    # Within-domain link hierarchy: which doc_types link to which within the same domain.
    # as-is-detail links back to the as-is overview for the same domain.
    _WITHIN_DOMAIN_LINKS: dict[str, list[str]] = {
        "as-is-detail": ["as-is"],
        "as-is-schema": ["as-is"],
        "interface":    ["spec"],
        "data-model":   ["spec"],
    }

    # Pre-compute doc_ids so cross-references in links_to resolve consistently.
    _domain_doctype_id: dict[tuple[str, str], str] = {
        (r.domain, r.doc_type): _make_doc_id(project_slug, r.domain, r.doc_type)
        for r in rollups
    }

    results: list[dict] = []

    for rollup in rollups:
        # Compute links_to: within-domain hierarchy + cross-domain as-is links
        links_to: list[str] = []
        for target_type in _WITHIN_DOMAIN_LINKS.get(rollup.doc_type, []):
            target_id = _domain_doctype_id.get((rollup.domain, target_type))
            if target_id:
                links_to.append(target_id)

        # Cross-domain: as-is docs link to as-is docs of domains they call
        if rollup.doc_type == "as-is" and domain_adjacency:
            for callee_domain in domain_adjacency.get(rollup.domain, []):
                callee_asis = _domain_doctype_id.get((callee_domain, "as-is"))
                if callee_asis and callee_asis not in links_to:
                    links_to.append(callee_asis)

        doc_id, markdown = render_doc(
            rollup,
            project_slug,
            repo_url=repo_url,
            repo_commit=repo_commit,
            unverified_claims=rollup.unverified_claims,
            links_to=links_to,
        )

        prefix_dir = output_dir / _doc_type_prefix(rollup.doc_type)
        prefix_dir.mkdir(parents=True, exist_ok=True)
        file_path = prefix_dir / f"{doc_id}.md"
        file_path.write_text(markdown, encoding="utf-8")

        results.append(
            {
                "doc_id": doc_id,
                "doc_type": rollup.doc_type,
                "domain": rollup.domain,
                "file_path": str(file_path),
                "confidence": rollup.confidence,
            }
        )
        logger.info("Wrote %s -> %s", doc_id, file_path)

    return results


def write_scenario_docs(
    scenario_flows: list,
    artifacts: dict[str, dict],
    output_dir: Path,
    project_slug: str,
    repo_url: str = "",
    repo_commit: str = "",
) -> list[dict]:
    """Render and write process-flow docs from ScenarioFlow objects.

    Returns list of dicts with: doc_id, doc_type, domain, file_path, confidence.
    """
    if not scenario_flows:
        return []

    env = _get_template_env()
    template = env.get_template("process-flow.md.j2")
    prefix_dir = output_dir / _doc_type_prefix("process-flow")
    prefix_dir.mkdir(parents=True, exist_ok=True)
    scan_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    results: list[dict] = []

    for i, flow in enumerate(scenario_flows, start=1):
        art = artifacts.get(flow.scenario_id, {})
        content_parts: list[str] = []

        if flow.steps:
            step_lines = ["## Business Steps\n"]
            for s in flow.steps:
                step_lines.append(
                    f"**{s.get('step', i)}. {s.get('name', 'Step')}**"
                    f" ({s.get('type', '')}) — {s.get('description', '')}"
                )
            content_parts.append("\n".join(step_lines))

        if art.get("mermaid"):
            content_parts.append(
                f"## Sequence Diagram\n\n```mermaid\n{art['mermaid']}\n```"
            )

        if art.get("ipo"):
            content_parts.append(art["ipo"])

        if art.get("plantuml"):
            content_parts.append(
                f"## Activity Diagram\n\n```plantuml\n{art['plantuml']}\n```"
            )

        content = "\n\n".join(content_parts)
        scenario_slug = _slugify(flow.scenario_id) or f"flow-{i:03d}"
        doc_id = _slugify(f"{project_slug}-{scenario_slug}-process-flow")
        rendered = template.render(
            doc_id=doc_id,
            title=f"Process Flow: {flow.scenario_id}",
            domain=flow.domain or "",
            scan_date=scan_date,
            repo_url=repo_url,
            repo_commit=repo_commit,
            confidence=flow.confidence,
            links_to=[],
            content=content,
        )
        file_path = prefix_dir / f"{doc_id}.md"
        file_path.write_text(rendered, encoding="utf-8")
        results.append(
            {
                "doc_id": doc_id,
                "doc_type": "process-flow",
                "domain": flow.domain or "",
                "file_path": str(file_path),
                "confidence": flow.confidence,
            }
        )
        logger.info("Wrote %s -> %s", doc_id, file_path)

    return results
