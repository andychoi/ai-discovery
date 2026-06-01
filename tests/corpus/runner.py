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

from ai_discovery.extractors import extract_relationships
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
    edges = build_call_graph(nodes)
    relationships = extract_relationships(nodes)
    return ExtractionResult(nodes=nodes, edges=edges, relationships=relationships)
