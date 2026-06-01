"""Corpus accuracy harness — extraction runner (HIGH-10, Phase 1).

Runs the REAL extraction pipeline (no LLM, no network, no mocks) over a fixture
project and returns the structured facts an accuracy check compares against
human-labeled ground truth:

    walk_repo -> per-language parse_file -> build_call_graph + extract_relationships

This is deliberately the deterministic core of the pipeline — the layer the
verified-facts machinery and every downstream doc depend on. If it drifts, the
whole product's faithfulness drifts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ai_discovery.extractors import (
    extract_external_systems,
    extract_relationships,
    read_infra_files,
    read_openapi_files,
)
from ai_discovery.graph.call_graph import build_call_graph
from ai_discovery.graph.models import CallEdge, CodeNode, EntityRelationship
from ai_discovery.repo.file_walker import walk_repo

# Map a parser language key to its parser class (lazy import to keep this light).
_PARSERS = {
    "java": ("ai_discovery.parsers.java", "JavaParser"),
    "csharp": ("ai_discovery.parsers.csharp", "CSharpParser"),
    "python": ("ai_discovery.parsers.python_parser", "PythonParser"),
    "javascript": ("ai_discovery.parsers.javascript", "JavaScriptParser"),
}


@dataclass
class ExtractionResult:
    nodes: list[CodeNode] = field(default_factory=list)
    edges: list[CallEdge] = field(default_factory=list)
    relationships: list[EntityRelationship] = field(default_factory=list)
    external_systems: list[CodeNode] = field(default_factory=list)

    def external_system_pairs(self) -> set[tuple[str, str]]:
        """{(system display name, kind)} for synthesized external-system nodes."""
        return {
            (n.framework_hints.get("system", n.name), n.framework_hints.get("kind", ""))
            for n in self.external_systems
        }

    # ---- derived views the metrics layer matches against ground truth ----

    def endpoint_pairs(self) -> set[tuple[str, str]]:
        """{(HTTP_METHOD, path)} for every endpoint node carrying both hints."""
        out: set[tuple[str, str]] = set()
        for n in self.nodes:
            if n.node_type != "endpoint":
                continue
            h = n.framework_hints or {}
            method, route = h.get("method"), h.get("route")
            if method and route:
                out.add((str(method), str(route)))
        return out

    def entity_fields(self) -> dict[str, set[str]]:
        """{table_or_class_name: {field names}} for db_model nodes."""
        out: dict[str, set[str]] = {}
        for n in self.nodes:
            if n.node_type != "db_model":
                continue
            name = str((n.framework_hints or {}).get("table") or n.name)
            out[name] = set(n.fields or [])
        return out

    def relationship_pairs(self) -> set[tuple[str, str]]:
        return {(r.from_entity, r.to_entity) for r in self.relationships}


def _parser_for(language: str):
    mod_name, cls_name = _PARSERS[language]
    mod = __import__(mod_name, fromlist=[cls_name])
    return getattr(mod, cls_name)()


def run_fixture(repo_path: Path, language: str) -> ExtractionResult:
    """Parse one fixture with the real API. `language` is the parser key."""
    parser = _parser_for(language)
    nodes: list[CodeNode] = []
    for f in walk_repo(repo_path, {language}):
        try:
            nodes.extend(parser.parse_file(f))
        except Exception:  # a single unparseable file must not abort the run
            continue
    # HIGH-7: merge OpenAPI/Swagger spec endpoints (dedup vs AST by method+route).
    ast_routes = {
        (n.framework_hints.get("method"), n.framework_hints.get("route"))
        for n in nodes if n.node_type == "endpoint"
    }
    for n in read_openapi_files(Path(repo_path)):
        if (n.framework_hints.get("method"), n.framework_hints.get("route")) not in ast_routes:
            nodes.append(n)
    edges = build_call_graph(nodes)
    relationships = extract_relationships(nodes)
    ext_nodes, ext_edges = extract_external_systems(nodes)
    # HIGH-7: merge deployment-declared backing services, deduped by identity.
    by_qn = {n.qualified_name: n for n in ext_nodes}
    for n in read_infra_files(Path(repo_path)):
        by_qn.setdefault(n.qualified_name, n)
    return ExtractionResult(
        nodes=nodes, edges=edges + ext_edges, relationships=relationships,
        external_systems=list(by_qn.values()),
    )
