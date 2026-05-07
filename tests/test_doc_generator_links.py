"""Track 5 — cross-artifact links_to graph.

PF docs link to their domain's ASIS/ASD/ASSC; ASD/ASSC docs link forward
to the PF scenarios in the same domain. This makes the artifact tree
navigable: a reader on a process-flow can jump to overview/detail/schema,
and a reader on a use-case doc can jump to the implementing flows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ai_discovery.ai.rollup import RollupResult
from ai_discovery.generators.doc_generator import (
    write_docs,
    write_scenario_docs,
)


# Minimal stand-ins so we don't import the full ScenarioFlow dataclass tree
@dataclass
class _MockFlow:
    scenario_id: str
    domain: str | None = None
    confidence: float = 1.0
    steps: list = field(default_factory=list)
    input: list = field(default_factory=list)
    process: list = field(default_factory=list)
    output: list = field(default_factory=list)
    data_flow: list = field(default_factory=list)
    external_interfaces: list = field(default_factory=list)


def _rollup(domain: str, doc_type: str) -> RollupResult:
    return RollupResult(
        domain=domain,
        doc_type=doc_type,
        title=f"{domain} — {doc_type}",
        content_md="body",
        confidence=0.9,
        tokens_in=0, tokens_out=0, model="test",
    )


def _read_links_to(md_path: Path) -> list[str]:
    """Extract the links_to YAML array from a markdown file's frontmatter."""
    text = md_path.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("links_to:"):
            raw = line.split(":", 1)[1].strip()
            # Frontmatter renders as JSON array via tojson filter; safe to eval-via-json
            import json
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return []
    return []


# ---------------------------------------------------------------------------
# PF -> ASIS / ASD / ASSC
# ---------------------------------------------------------------------------

def test_pf_doc_links_back_to_domain_rollups(tmp_path: Path):
    flows = [_MockFlow(scenario_id="addToCart", domain="cart")]
    write_scenario_docs(
        flows, artifacts={}, output_dir=tmp_path,
        project_slug="myapp",
        rollup_domains={"cart"},
    )
    pf_file = tmp_path / "PF" / "cart-addtocart.md"
    assert pf_file.exists()
    links = _read_links_to(pf_file)
    assert "myapp-cart-as-is" in links
    assert "myapp-cart-as-is-detail" in links
    assert "myapp-cart-as-is-schema" in links


def test_pf_doc_skips_links_when_no_matching_rollup(tmp_path: Path):
    """If a domain has no rollups, the PF shouldn't emit dead links."""
    flows = [_MockFlow(scenario_id="addToCart", domain="cart")]
    write_scenario_docs(
        flows, artifacts={}, output_dir=tmp_path,
        project_slug="myapp",
        rollup_domains=set(),  # no rollups exist
    )
    pf_file = tmp_path / "PF" / "cart-addtocart.md"
    links = _read_links_to(pf_file)
    assert links == []


def test_pf_doc_with_no_domain_emits_empty_links(tmp_path: Path):
    flows = [_MockFlow(scenario_id="orphan", domain=None)]
    write_scenario_docs(
        flows, artifacts={}, output_dir=tmp_path,
        project_slug="myapp",
        rollup_domains={"cart"},
    )
    pf_file = tmp_path / "PF" / "orphan.md"
    assert _read_links_to(pf_file) == []


# ---------------------------------------------------------------------------
# ASD / ASSC -> PF
# ---------------------------------------------------------------------------

def test_as_is_detail_links_forward_to_pf_scenarios(tmp_path: Path):
    rollups = [
        _rollup("cart", "as-is"),
        _rollup("cart", "as-is-detail"),
        _rollup("cart", "as-is-schema"),
    ]
    flows = [
        _MockFlow(scenario_id="addToCart", domain="cart"),
        _MockFlow(scenario_id="updateCartItem", domain="cart"),
    ]
    write_docs(rollups, tmp_path, "myapp", scenario_flows=flows)
    asd_file = tmp_path / "ASD" / "cart.md"
    links = _read_links_to(asd_file)
    # within-domain: ASD -> ASIS still works
    assert "myapp-cart-as-is" in links
    # forward: ASD -> PF for both scenarios in this domain
    assert "myapp-addtocart-process-flow" in links
    assert "myapp-updatecartitem-process-flow" in links


def test_as_is_overview_does_not_get_pf_links(tmp_path: Path):
    """Forward-linking is only on detail/schema docs (overview stays high-level)."""
    rollups = [_rollup("cart", "as-is")]
    flows = [_MockFlow(scenario_id="addToCart", domain="cart")]
    write_docs(rollups, tmp_path, "myapp", scenario_flows=flows)
    asis_file = tmp_path / "ASIS" / "cart.md"
    links = _read_links_to(asis_file)
    # No PF links on as-is overview — only domain_adjacency-driven cross-domain links
    assert all("process-flow" not in link for link in links)


def test_as_is_schema_skips_bootstrap_scenarios(tmp_path: Path):
    """Bootstrap scenarios (main, addresourcehandlers) are filtered before linking,
    so ASSC doesn't point at PF docs that won't exist on disk."""
    rollups = [_rollup("cart", "as-is-schema")]
    flows = [
        _MockFlow(scenario_id="addToCart", domain="cart"),
        _MockFlow(scenario_id="main", domain="cart"),  # bootstrap, gets filtered
    ]
    write_docs(rollups, tmp_path, "myapp", scenario_flows=flows)
    assc_file = tmp_path / "ASSC" / "cart.md"
    links = _read_links_to(assc_file)
    assert "myapp-addtocart-process-flow" in links
    # main is filtered as bootstrap and should not appear
    assert all("main" not in link for link in links)


def test_no_scenario_flows_keeps_legacy_link_behavior(tmp_path: Path):
    """Without scenario_flows passed in, ASD only gets within-domain links."""
    rollups = [_rollup("cart", "as-is"), _rollup("cart", "as-is-detail")]
    write_docs(rollups, tmp_path, "myapp")  # no scenario_flows
    asd_file = tmp_path / "ASD" / "cart.md"
    links = _read_links_to(asd_file)
    assert links == ["myapp-cart-as-is"]
