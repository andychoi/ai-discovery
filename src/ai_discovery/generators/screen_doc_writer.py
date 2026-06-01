"""
Write screen-centric specifications to markdown files.

Handles Jinja2 template rendering, MANUAL block preservation,
and source hash frontmatter generation.
"""

import re
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader

from ..ai.screen_spec_generator import ScreenSpec


def load_existing_manual_blocks(
    screen_id: str,
    docs_dir: Path,
) -> dict[str, str]:
    """
    Extract all <!-- MANUAL:* --> blocks from existing screen spec.

    Returns {block_name: content, ...}

    Example: {
        "business-context": "User notes...",
        "layout-notes": "...",
    }
    """
    screen_file = docs_dir / "screens" / f"{screen_id}.md"
    if not screen_file.exists():
        return {}

    content = screen_file.read_text()
    manual_blocks = {}

    # Regex: <!-- MANUAL:(.+?) -->...<!-- /MANUAL:\1 -->
    pattern = r"<!-- MANUAL:(.+?) -->(.*?)<!-- /MANUAL:\1 -->"
    for match in re.finditer(pattern, content, re.DOTALL):
        block_name = match.group(1)
        block_content = match.group(2).strip()
        manual_blocks[block_name] = block_content

    return manual_blocks


def write_screen_spec(
    spec: ScreenSpec,
    docs_dir: Path,
    project_slug: str,
    repo_url: str = "",
    repo_commit: str = "",
) -> Path:
    """
    Render screen spec to markdown with MANUAL blocks preserved.

    Args:
        spec: ScreenSpec object with all prose and metadata
        docs_dir: Root docs output directory
        project_slug: Project identifier (for doc_id)
        repo_url: Repository URL for frontmatter
        repo_commit: Commit SHA for frontmatter

    Returns:
        Path to written file
    """
    # Setup Jinja2 environment
    templates_dir = Path(__file__).parent / "templates"
    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        keep_trailing_newline=True,
    )
    template = env.get_template("screen-spec.md.j2")

    # Load existing MANUAL blocks to preserve user edits
    manual_blocks = load_existing_manual_blocks(spec.screen_id, docs_dir)

    # Build related docs links
    related_docs = {
        "backend_specs": [
            {"doc_id": f"{project_slug}-be-{c.class_name}", "title": c.class_name}
            for c in spec.be_controllers
        ],
        "batch_jobs": [
            {"doc_id": f"{project_slug}-batch-{job}", "title": job}
            for job in spec.batch_jobs
        ],
        "databases": [
            {"doc_id": f"{project_slug}-db-{table}", "title": table}
            for table in spec.db_tables
        ],
        "interfaces": [
            {"doc_id": f"{project_slug}-interface-{interface}", "title": interface}
            for interface in spec.external_interfaces
        ],
    }

    # Render template with spec data + MANUAL blocks
    rendered = template.render(
        doc_id=f"{project_slug}-screen-{spec.screen_id}",
        title=spec.screen_label,
        menu_path=spec.menu_path,
        crud_profile=spec.crud_profile,
        interaction_mode=spec.interaction_mode,
        status="Draft",
        # CRIT-3: screen specs are LLM narrative not yet claim-verified; never
        # publish the LLM's self-asserted confidence at face value — cap it.
        confidence=min(getattr(spec, "confidence", 0.5) or 0.5, 0.5),
        purpose=spec.purpose,
        when_used=spec.when_used,
        user_actions=spec.user_actions,
        rules_narrative=spec.rules_narrative,
        rules=spec.rules,
        fields_description=spec.fields_description,
        database_tables=spec.db_tables,
        downstream_effects=spec.downstream_effects,
        batch_jobs=spec.batch_jobs,
        external_interfaces=spec.external_interfaces,
        fe_component=spec.fe_component,
        fe_api_calls=spec.fe_api_calls,
        be_controllers=spec.be_controllers,
        be_services=spec.be_services,
        permissions=[],
        related_docs=related_docs,
        source_hashes=spec.source_hashes,
        open_items=spec.open_items,
        repo_url=repo_url,
        repo_commit=repo_commit,
        # Inject MANUAL blocks
        manual_blocks=manual_blocks,
    )

    # Write to disk
    screens_dir = docs_dir / "screens"
    screens_dir.mkdir(parents=True, exist_ok=True)
    output_file = screens_dir / f"{spec.screen_id}.md"
    output_file.write_text(rendered, encoding="utf-8")

    return output_file


def write_all_screen_specs(
    specs: list[ScreenSpec],
    docs_dir: Path,
    project_slug: str,
    repo_url: str = "",
    repo_commit: str = "",
) -> list[dict]:
    """
    Write all screen specs to markdown files.

    Returns list of {screen_id, file_path, status} dicts.
    """
    results = []

    for spec in specs:
        try:
            file_path = write_screen_spec(
                spec,
                docs_dir,
                project_slug,
                repo_url=repo_url,
                repo_commit=repo_commit,
            )
            results.append(
                {
                    "screen_id": spec.screen_id,
                    "file_path": str(file_path),
                    "status": "written",
                }
            )
        except Exception as e:
            results.append(
                {
                    "screen_id": spec.screen_id,
                    "file_path": None,
                    "status": "error",
                    "error": str(e),
                }
            )

    return results
