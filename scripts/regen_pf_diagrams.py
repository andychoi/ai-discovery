"""One-shot regen of activity diagrams + PF markdown for an existing scan.

Reads scenario_flows from the discovery DB, runs the current
BPMNGenerator.generate_mermaid_flowchart() over each, updates the
mermaid_flowchart column, then re-renders the PF markdown via
doc_generator.write_scenario_docs.

Faster than `discover scan --resume-from 15` when only the activity
diagram emitter changed. No LLM calls, no Phase 14/17 re-execution.

Usage:
    python scripts/regen_pf_diagrams.py <project_slug>
"""

from __future__ import annotations

import sys
from pathlib import Path

from ai_discovery.ai.flow_analyzer import load_scenario_flows
from ai_discovery.db import get_conn, init_db
from ai_discovery.generators.bpmn_generator import BPMNGenerator
from ai_discovery.generators.doc_generator import write_scenario_docs


def main(slug: str) -> None:
    db_path = Path(f"data/discovery-output/output-{slug}/discovery-{slug}.db")
    docs_dir = Path(f"data/output-{slug}")

    if not db_path.exists():
        sys.exit(f"DB not found: {db_path}")

    init_db(db_path)

    conn = get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT id, repo_url, repo_path, commit_sha FROM scan_runs "
            "WHERE project_slug = ? AND status = 'completed' "
            "ORDER BY id DESC LIMIT 1",
            (slug,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        sys.exit(f"No completed scan_run for slug={slug}")
    scan_id = row["id"]
    repo_url = row["repo_url"] or row["repo_path"] or ""
    repo_commit = row["commit_sha"] or ""
    print(f"Using scan_id={scan_id}, repo_commit={repo_commit[:10]}")

    flows, artifacts = load_scenario_flows(scan_id, db_path)
    print(f"Loaded {len(flows)} scenario flows")

    bpmn_gen = BPMNGenerator()
    conn = get_conn(db_path)
    try:
        for flow in flows:
            new_fc = bpmn_gen.generate_mermaid_flowchart(flow)
            conn.execute(
                "UPDATE scenario_flows SET mermaid_flowchart = ? "
                "WHERE scan_id = ? AND scenario_id = ?",
                (new_fc, scan_id, flow.scenario_id),
            )
            artifacts.setdefault(flow.scenario_id, {})["mermaid_flowchart"] = new_fc
        conn.commit()
    finally:
        conn.close()
    print(f"Refreshed mermaid_flowchart for {len(flows)} scenarios in DB")

    written = write_scenario_docs(
        flows,
        artifacts,
        docs_dir,
        slug,
        repo_url=repo_url,
        repo_commit=repo_commit,
    )
    print(f"Wrote {len(written)} PF markdown files to {docs_dir}/PF/")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python scripts/regen_pf_diagrams.py <project_slug>")
    main(sys.argv[1])
