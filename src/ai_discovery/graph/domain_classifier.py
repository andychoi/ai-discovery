"""Domain classifier — infers business domains from file paths and namespaces."""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import PurePosixPath

from .models import CodeNode, Domain

_FRAMEWORK_DIRS = frozenset({
    "controllers", "routers", "models", "handlers", "services",
    "api", "views", "middleware", "utils", "helpers", "common",
    "shared", "core", "config", "src", "main", "app",
    # C# / CQRS conventions
    "Commands", "Queries", "Dtos", "Entities", "Repositories",
    # Also match lowercase variants
    "commands", "queries", "dtos", "entities", "repositories",
    # Additional common framework dirs
    "controller", "router", "routes", "route", "model",
    "handler", "service", "schemas", "schema", "view",
    "apis", "lib",
})

# Common filename suffixes to strip when falling back to stem
_SUFFIX_RE = re.compile(
    r"_(routes|router|views|api|model|models|handler|handlers|service|services|"
    r"controller|controllers)$"
)

_ENTRY_POINT_TYPES = frozenset({"endpoint", "batch_job"})


def infer_domain(qualified_name: str, file_path: str) -> str:
    """Infer domain from file path or qualified name.

    Strategy:
    1. Try namespace/package from qualified_name (e.g., "com.corp.payments.PaymentService" -> "payments")
    2. Walk file path components, skip framework dirs, return first meaningful segment
    3. Fallback: file stem
    """
    # Strategy 1: namespace / package segments from qualified_name
    if "." in qualified_name:
        parts = qualified_name.split(".")
        # Walk segments (skip last which is the class/function name itself)
        for part in parts[:-1]:
            if part.lower() not in _FRAMEWORK_DIRS and not _is_tld_segment(part):
                return part

    # Strategy 2: walk file path components, skip framework dirs
    path_parts = PurePosixPath(file_path).parts
    for part in path_parts[:-1]:  # exclude filename
        if part.lower() not in _FRAMEWORK_DIRS:
            return part

    # Strategy 3: fallback to filename stem with common suffixes stripped
    stem = PurePosixPath(path_parts[-1]).stem if path_parts else qualified_name
    stem = _SUFFIX_RE.sub("", stem)
    return stem


def _is_tld_segment(segment: str) -> bool:
    """Return True for common top-level package segments like 'com', 'org', 'net'."""
    return segment.lower() in {
        "com", "org", "net", "io", "co", "corp", "internal",
        "example", "company", "enterprise",
    }


def classify_domains(nodes: list[CodeNode]) -> dict[str, Domain]:
    """Group nodes into domains. Returns dict[domain_name, Domain].

    For each domain, populates:
    - nodes: all CodeNodes in this domain
    - entry_points: nodes where node_type is "endpoint" or "batch_job"
    - db_models: nodes where node_type is "db_model"
    - tech_stack: aggregate of languages/frameworks seen
    """
    groups: dict[str, list[CodeNode]] = defaultdict(list)

    for node in nodes:
        domain_name = infer_domain(node.qualified_name, node.file_path)
        node.domain = domain_name
        groups[domain_name].append(node)

    domains: dict[str, Domain] = {}
    for name, domain_nodes in groups.items():
        entry_points = [n for n in domain_nodes if n.node_type in _ENTRY_POINT_TYPES]
        db_models = [n for n in domain_nodes if n.node_type == "db_model"]

        # Aggregate tech stack: languages and frameworks
        languages: set[str] = set()
        frameworks: set[str] = set()
        for n in domain_nodes:
            if n.language:
                languages.add(n.language)
            for fw in n.framework_hints.get("frameworks", []):
                frameworks.add(fw)

        tech_stack: dict[str, list[str]] = {}
        if languages:
            tech_stack["languages"] = sorted(languages)
        if frameworks:
            tech_stack["frameworks"] = sorted(frameworks)

        domains[name] = Domain(
            name=name,
            nodes=domain_nodes,
            entry_points=entry_points,
            db_models=db_models,
            tech_stack=tech_stack,
        )

    return domains
