"""Cross-repo integration correlator (HIGH-8, enterprise federation).

The federation merge stitched entities by name coincidence; it never linked two
services that actually *call each other*. This correlates one repo's OUTBOUND
HTTP calls (consumer) against another repo's INBOUND endpoints (provider) by
matching HTTP method + normalized path, producing real provider→consumer
integration edges — the actual enterprise deliverable.

Path matching is structural: scheme/host are stripped, path parameters are
collapsed (`/orders/{id}`, `/orders/:id`, `/orders/42`, `/orders/<uuid>` all
normalize to `/orders/{}`), so a consumer calling `GET https://api/orders/42`
matches a provider exposing `GET /orders/{id}`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


@dataclass
class Endpoint:
    method: str
    path: str
    handler: str = ""


@dataclass
class OutboundCall:
    method: str
    target: str
    caller: str = ""


@dataclass
class RepoInterfaces:
    slug: str
    inbound: list[Endpoint] = field(default_factory=list)
    outbound: list[OutboundCall] = field(default_factory=list)


@dataclass
class IntegrationEdge:
    provider_repo: str
    consumer_repo: str
    method: str
    path: str            # the provider's (normalized) endpoint
    consumer_caller: str = ""
    provider_handler: str = ""
    confidence: float = 0.85


def normalize_path(path: str) -> str:
    """Structural form of a route/URL for matching: drop scheme+host+query,
    lowercase, collapse every path parameter to `{}`, strip trailing slash."""
    p = (path or "").strip()
    if "://" in p:                       # https://host/a/b → /a/b
        rest = p.split("://", 1)[1]
        p = "/" + rest.split("/", 1)[1] if "/" in rest else "/"
    p = p.split("?", 1)[0].split("#", 1)[0]
    p = p.lower().rstrip("/") or "/"
    out = []
    for seg in p.split("/"):
        if not seg:
            continue
        if (seg.startswith("{") or seg.startswith(":")          # {id} / :id
                or seg.startswith("<") or seg.isdigit()          # <id> / 42
                or _UUID.match(seg)):
            out.append("{}")
        else:
            out.append(seg)
    return "/" + "/".join(out)


def correlate(repos: list[RepoInterfaces]) -> list[IntegrationEdge]:
    """Return provider→consumer edges where a consumer's outbound call matches a
    provider's inbound endpoint (method + normalized path), across distinct repos."""
    # Provider index: (method, normalized path) -> [(repo, endpoint)]
    providers: dict[tuple[str, str], list[tuple[str, Endpoint]]] = {}
    for repo in repos:
        for ep in repo.inbound:
            key = (ep.method.upper(), normalize_path(ep.path))
            providers.setdefault(key, []).append((repo.slug, ep))

    edges: list[IntegrationEdge] = []
    seen: set[tuple[str, str, str, str]] = set()
    for consumer in repos:
        for call in consumer.outbound:
            if not call.target:
                continue
            key = (call.method.upper(), normalize_path(call.target))
            for provider_slug, ep in providers.get(key, []):
                if provider_slug == consumer.slug:
                    continue  # intra-repo call, not a cross-repo integration
                dedup = (provider_slug, consumer.slug, key[0], key[1])
                if dedup in seen:
                    continue
                seen.add(dedup)
                edges.append(IntegrationEdge(
                    provider_repo=provider_slug, consumer_repo=consumer.slug,
                    method=key[0], path=key[1],
                    consumer_caller=call.caller, provider_handler=ep.handler,
                ))
    return edges


def interfaces_to_dict(ri: RepoInterfaces) -> dict:
    return {
        "slug": ri.slug,
        "inbound": [{"method": e.method, "path": e.path, "handler": e.handler} for e in ri.inbound],
        "outbound": [{"method": o.method, "target": o.target, "caller": o.caller} for o in ri.outbound],
    }


def interfaces_from_dict(d: dict) -> RepoInterfaces:
    return RepoInterfaces(
        slug=str(d.get("slug", "")),
        inbound=[Endpoint(**e) for e in d.get("inbound", [])],
        outbound=[OutboundCall(**o) for o in d.get("outbound", [])],
    )


def build_repo_interfaces(slug: str, nodes: list, edges: list) -> RepoInterfaces:
    """Derive a repo's interface surface from its scanned nodes + edges.

    Inbound = endpoint nodes (method+route, AST or OpenAPI). Outbound = external
    HTTP-call edges carrying a `target` URL/path (set by the external-system
    extractor for http_api clients).
    """
    inbound: list[Endpoint] = []
    for n in nodes:
        if getattr(n, "node_type", "") != "endpoint":
            continue
        h = getattr(n, "framework_hints", None) or {}
        method, route = h.get("method"), h.get("route")
        if method and route:
            inbound.append(Endpoint(method=str(method), path=str(route), handler=n.name))

    outbound: list[OutboundCall] = []
    for e in edges:
        if getattr(e, "edge_type", "") != "external_call":
            continue
        meta = getattr(e, "metadata", None) or {}
        target = meta.get("target")
        if not target:
            continue
        outbound.append(OutboundCall(
            method=str(meta.get("operation", "")).upper(),
            target=str(target), caller=e.caller,
        ))
    return RepoInterfaces(slug=slug, inbound=inbound, outbound=outbound)
