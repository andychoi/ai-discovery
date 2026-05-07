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
    # Track 2: Phase 3 visual artifacts get DocHub-style folders so they
    # ship alongside ASIS/ASD/ASSC/PF instead of as flat files at scan root.
    "entity-backbone": "BPMN",
    "entity-decisions": "DMN",
    "entity-ears": "EARS",
    "entity-impact": "IMPACT",
}


def _slugify(text: str) -> str:
    """Lowercase, collapse non-alphanumeric to hyphens, strip edge hyphens."""
    if not text:
        return ""
    return re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")


def _doc_type_prefix(doc_type: str) -> str:
    return _DOC_TYPE_PREFIXES.get(doc_type, doc_type.upper().replace("-", ""))


def _make_doc_id(project_slug: str, domain: str, doc_type: str) -> str:
    """Slugified DB identifier — stable across runs; remains
    `{project_slug}-{domain}-{doc_type}` so existing DocHub IDs don't break."""
    return _slugify(f"{project_slug}-{domain}-{doc_type}")


def _make_filename(domain: str, doc_type: str) -> str:
    """Track 3: on-disk filename, decoupled from doc_id.

    The DocHub-style folder (`ASIS/`, `ASD/`, `ASSC/`, `PF/`) already names
    the type, and the output root folder names the project. So filenames only
    need the domain — no project prefix, no type suffix.

    For PF docs the caller appends `{scenario}` since one PF folder holds
    many scenarios per domain; see `_make_scenario_filename`.
    """
    base = _slugify(domain) or doc_type
    return f"{base}.md"


def _make_scenario_filename(domain: str | None, scenario_id: str, fallback_index: int) -> str:
    """Track 3: domain-grouped scenario filename: `{domain}-{scenario}.md`.

    Domain-grouping puts related scenarios next to each other alphabetically
    (`cart-addtocart.md`, `cart-deletecartitem.md`, …). The folder `PF/`
    already implies process-flow; no `-process-flow` suffix.
    """
    scenario_slug = _slugify(scenario_id) or f"flow-{fallback_index:03d}"
    domain_slug = _slugify(domain or "")
    if domain_slug:
        return f"{domain_slug}-{scenario_slug}.md"
    return f"{scenario_slug}.md"


# Track 3: scenarios that aren't real business flows. Filtered at write
# time so they don't pollute PF/. Names match the slugified scenario_id.
_BOOTSTRAP_SCENARIO_NAMES: frozenset[str] = frozenset({
    "main",                  # Spring Boot @SpringBootApplication.main
    "addresourcehandlers",   # Swagger / WebMvcConfigurer resource handler registration
    "configure",             # generic Spring config callback
    "corsconfigurer",        # CORS bean config
    "addviewcontrollers",    # MVC view config
    "apidocket",             # SwaggerConfig.Docket bean
})


def _is_bootstrap_scenario(scenario_id: str) -> bool:
    """Track 3: identify scenarios that are config/bootstrap, not business flows.

    Match against the raw scenario name (last hyphen-separated segment) since
    upstream may prepend a counter. We compare slug-form to handle case and
    punctuation variation across parsers.
    """
    if not scenario_id:
        return True
    slug = _slugify(scenario_id)
    # Strip common upstream prefixes ("scenario-", "flow-") and trailing
    # numeric counters ("-42") that could surround a bootstrap name.
    for prefix in ("scenario-", "flow-"):
        if slug.startswith(prefix):
            slug = slug[len(prefix):]
    slug = re.sub(r"-\d+$", "", slug)
    return slug in _BOOTSTRAP_SCENARIO_NAMES


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
    scenario_flows: list | None = None,
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

    # Track 5: index PF doc_ids by domain so as-is-detail can link to its
    # scenarios. Filter bootstrap scenarios with the same rule write_scenario_docs
    # uses, so links don't point at docs that won't actually be written.
    _pf_ids_by_domain: dict[str, list[str]] = {}
    if scenario_flows:
        for flow in scenario_flows:
            if _is_bootstrap_scenario(flow.scenario_id):
                continue
            domain = flow.domain or ""
            scenario_slug = _slugify(flow.scenario_id)
            if not scenario_slug:
                continue
            pf_id = _slugify(f"{project_slug}-{scenario_slug}-process-flow")
            _pf_ids_by_domain.setdefault(domain, []).append(pf_id)

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

        # Track 5: as-is-detail and as-is-schema link forward to PF scenarios
        # in the same domain — bidirectional navigation between use cases and
        # the process flows that implement them.
        if rollup.doc_type in ("as-is-detail", "as-is-schema"):
            for pf_id in _pf_ids_by_domain.get(rollup.domain, []):
                if pf_id not in links_to:
                    links_to.append(pf_id)

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
        # Track 3: filename is the domain only (folder names the type, root
        # names the project). doc_id stays full-form for DB / DocHub.
        file_path = prefix_dir / _make_filename(rollup.domain, rollup.doc_type)
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
    mining_results: dict | None = None,
    rollup_domains: set[str] | None = None,
) -> list[dict]:
    """Render and write process-flow docs from ScenarioFlow objects.

    Args:
        scenario_flows: List of ScenarioFlow objects
        artifacts: dict[scenario_id] → {mermaid, plantuml, ipo, bpmn}
        output_dir: Output directory
        project_slug: Project slug for doc ID
        repo_url: Repository URL
        repo_commit: Commit SHA
        mining_results: dict[scenario_id] → MiningResult (optional)
        rollup_domains: Track 5 — set of domain names that have ASIS/ASD/ASSC
            rollups available; PF docs link back only to existing rollups so
            we don't emit dead links to nonexistent docs. None = link to all
            three doc types for whatever domain the scenario claims.

    Returns:
        list of dicts with: doc_id, doc_type, domain, file_path, confidence.
    """
    if not scenario_flows:
        return []

    # Track 3: filter bootstrap/config scenarios — they aren't business flows.
    filtered_flows = []
    for flow in scenario_flows:
        if _is_bootstrap_scenario(flow.scenario_id):
            logger.info("Skipping bootstrap scenario %r (not a business flow)", flow.scenario_id)
            continue
        filtered_flows.append(flow)
    if not filtered_flows:
        return []
    scenario_flows = filtered_flows

    mining_results = mining_results or {}
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

        # Include process mining analysis if available
        mining_result = mining_results.get(flow.scenario_id)
        if mining_result:
            from ..ai.mining_reporter import MiningReporter
            mining_md = MiningReporter.generate_markdown_report(mining_result)
            content_parts.append(mining_md)

        content = "\n\n".join(content_parts)
        # Track 3: doc_id (DB / DocHub identifier) keeps the full slugified
        # form for stability; on-disk filename is the domain-grouped form.
        scenario_slug = _slugify(flow.scenario_id) or f"flow-{i:03d}"
        doc_id = _slugify(f"{project_slug}-{scenario_slug}-process-flow")
        # Track 5: every PF doc links back to its domain's ASIS/ASD/ASSC so
        # readers can navigate from a scenario to overview, detail, and schema.
        # Only link to rollups that actually exist (rollup_domains tells us).
        pf_links_to: list[str] = []
        if flow.domain:
            for target_doc_type in ("as-is", "as-is-detail", "as-is-schema"):
                if rollup_domains is not None and flow.domain not in rollup_domains:
                    continue
                pf_links_to.append(_make_doc_id(project_slug, flow.domain, target_doc_type))
        rendered = template.render(
            doc_id=doc_id,
            title=f"Process Flow: {flow.scenario_id}",
            domain=flow.domain or "",
            scan_date=scan_date,
            repo_url=repo_url,
            repo_commit=repo_commit,
            confidence=flow.confidence,
            links_to=pf_links_to,
            content=content,
        )
        filename = _make_scenario_filename(flow.domain, flow.scenario_id, i)
        file_path = prefix_dir / filename
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
