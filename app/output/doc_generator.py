"""Render ai-docs-compatible markdown files from RollupResults using Jinja2."""

from __future__ import annotations

import logging
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
}



def _get_template_env() -> Environment:
    """Create Jinja2 environment pointing at templates/ dir."""
    template_dir = Path(__file__).parent / "templates"
    return Environment(loader=FileSystemLoader(str(template_dir)), keep_trailing_newline=True)


def render_doc(
    rollup: RollupResult,
    project_slug: str,
    seq_num: int = 1,
    repo_url: str = "",
    repo_commit: str = "",
    unverified_claims: int = 0,
    links_to: list[str] | None = None,
    scenario_artifacts: dict[str, dict] | None = None,
) -> tuple[str, str]:
    """Render a single RollupResult to markdown string.

    Returns (doc_id, markdown_content).
    doc_id format: f"{TYPE_PREFIX}-{seq_num:03d}" (e.g. "ASAP-001", "ASIS-002")
    matching DocHub's human-created doc convention.
    """
    env = _get_template_env()
    template_name = f"{rollup.doc_type}.md.j2"
    template = env.get_template(template_name)

    prefix = _DOC_TYPE_PREFIXES.get(rollup.doc_type, rollup.doc_type.upper().replace("-", ""))
    doc_id = f"{prefix}-{seq_num:03d}"

    scan_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Append scenario artifacts if relevant (e.g. for as-is or as-is-detail)
    content = rollup.content_md
    if scenario_artifacts and rollup.doc_type in ("as-is", "as-is-detail"):
        content += "\n\n## Execution Flows\n"
        for scenario_id, artifacts in scenario_artifacts.items():
            # Include if domain matches in ID or name (naive mapping)
            if rollup.domain.lower() in scenario_id.lower():
                content += f"\n### Scenario: {scenario_id}\n"
                content += f"\n#### Sequence Diagram\n\n```mermaid\n{artifacts['mermaid']}\n```\n"
                content += f"\n{artifacts['ipo']}\n"

    rendered = template.render(
        doc_id=doc_id,
        title=rollup.title,
        scan_date=scan_date,
        repo_url=repo_url,
        repo_commit=repo_commit,
        confidence=rollup.confidence,
        unverified_claims=unverified_claims,
        links_to=links_to or [],
        content=content,
    )

    return doc_id, rendered


def write_docs(
    rollups: list[RollupResult],
    output_dir: Path,
    project_slug: str,
    repo_url: str = "",
    repo_commit: str = "",
    domain_adjacency: dict[str, list[str]] | None = None,
    scenario_artifacts: dict[str, dict] | None = None,
) -> list[dict]:
    """Render and write all rollups to output_dir/{doc_type}/{doc_id}.md.

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

    # First pass: assign all doc_ids without rendering so links_to can reference them.
    _type_counters_pre: dict[str, int] = defaultdict(int)
    _domain_doctype_id: dict[tuple[str, str], str] = {}
    for rollup in rollups:
        _type_counters_pre[rollup.doc_type] += 1
        prefix = _DOC_TYPE_PREFIXES.get(rollup.doc_type, rollup.doc_type.upper().replace("-", ""))
        pre_id = f"{prefix}-{_type_counters_pre[rollup.doc_type]:03d}"
        _domain_doctype_id[(rollup.domain, rollup.doc_type)] = pre_id

    results: list[dict] = []
    type_counters: dict[str, int] = defaultdict(int)

    for rollup in rollups:
        type_counters[rollup.doc_type] += 1

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
            seq_num=type_counters[rollup.doc_type],
            repo_url=repo_url,
            repo_commit=repo_commit,
            unverified_claims=rollup.unverified_claims,
            links_to=links_to,
            scenario_artifacts=scenario_artifacts,
        )

        doc_type_dir = output_dir / rollup.doc_type
        doc_type_dir.mkdir(parents=True, exist_ok=True)
        file_path = doc_type_dir / f"{doc_id}.md"
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
