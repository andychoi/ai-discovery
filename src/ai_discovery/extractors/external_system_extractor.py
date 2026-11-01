"""Promote external-client calls to first-class external-system nodes (HIGH-8).

Unresolved calls on a known client library (`axios.get`, `kafkaTemplate.send`,
`redis.get`, `stripe.charges.create`, …) previously degraded to 0.5-confidence
string callees indistinguishable from parse misses, so generated architecture
docs silently omitted the system's external surface. This synthesizes a typed
external-system node per distinct system and an `external_call` edge to it, so
DBs / APIs / queues / caches / payment / storage / IdP dependencies become
queryable nodes and surface as genuinely-external edges in domain rollups.

Detection is by call RECEIVER (the object a call is made on), matched against a
registry of well-known client libraries. It is intentionally conservative —
only recognized clients promote — so we never invent an external system from an
ambiguous local call.
"""

from __future__ import annotations

import re

from ..graph.call_graph import make_edge
from ..graph.models import CallEdge, CodeNode

# receiver token (lowercased) -> (display name, kind)
_EXTERNAL_REGISTRY: dict[str, tuple[str, str]] = {
    # HTTP / REST clients
    "axios": ("HTTP API", "http_api"),
    "fetch": ("HTTP API", "http_api"),
    "got": ("HTTP API", "http_api"),
    "request": ("HTTP API", "http_api"),
    "superagent": ("HTTP API", "http_api"),
    "httpx": ("HTTP API", "http_api"),
    "aiohttp": ("HTTP API", "http_api"),
    "requests": ("HTTP API", "http_api"),
    "urllib": ("HTTP API", "http_api"),
    "resttemplate": ("HTTP API", "http_api"),
    "webclient": ("HTTP API", "http_api"),
    "feign": ("HTTP API", "http_api"),
    "httpclient": ("HTTP API", "http_api"),
    "okhttpclient": ("HTTP API", "http_api"),
    "restclient": ("HTTP API", "http_api"),
    # Databases / ORMs / drivers
    "prisma": ("Database", "database"),
    "sequelize": ("Database", "database"),
    "typeorm": ("Database", "database"),
    "knex": ("Database", "database"),
    "pg": ("PostgreSQL", "database"),
    "mongoose": ("MongoDB", "database"),
    "tableclient": ("Azure Table", "database"),
    # Cache
    "redis": ("Redis", "cache"),
    "ioredis": ("Redis", "cache"),
    "memcached": ("Memcached", "cache"),
    # Messaging / queues
    "kafka": ("Kafka", "queue"),
    "kafkatemplate": ("Kafka", "queue"),
    "kafkaproducer": ("Kafka", "queue"),
    "amqp": ("RabbitMQ", "queue"),
    "pika": ("RabbitMQ", "queue"),
    "jmstemplate": ("JMS", "queue"),
    "sqsclient": ("AWS SQS", "queue"),
    "sqs": ("AWS SQS", "queue"),
    "sns": ("AWS SNS", "queue"),
    "servicebus": ("Azure Service Bus", "queue"),
    # Payment / mail / storage / cloud / identity
    "stripe": ("Stripe", "payment"),
    "blobclient": ("Azure Blob", "storage"),
    "s3": ("AWS S3", "storage"),
    "boto3": ("AWS", "cloud"),
    "google": ("Google API", "cloud"),
    "sendgrid": ("SendGrid", "mail"),
    "nodemailer": ("SMTP Mail", "mail"),
    "auth0": ("Auth0", "identity"),
    "okta": ("Okta", "identity"),
}


# Library names of JS/Python clients. In Java/C# the same words are ordinary variable names (`request` is a
# MediatR command, `requests` a list) — there they never denote a client (M6.15; eShop: 10 of 30 graded wrong).
_DYNAMIC_ONLY = frozenset({"axios", "fetch", "got", "request", "requests", "superagent", "httpx", "aiohttp",
                           "urllib", "prisma", "sequelize", "typeorm", "knex", "pg", "mongoose", "ioredis",
                           "pika", "boto3", "nodemailer"})
_TYPED_LANGS = frozenset({"java", "csharp", "vbnet"})


_GLOBALS = frozenset({"fetch"})   # needs no import


def _imported_names(node: CodeNode) -> set[str]:
    """Lower-cased names a file's imports bind (alias, name, module root) — a library client must be one."""
    out: set[str] = set()
    for imp in node.imports or []:
        for v in (imp.get("alias"), imp.get("name"), (imp.get("module") or "").split(".")[0]):
            if v:
                out.add(v.lower())
    return out


def _match_external(receiver: str | None, language: str = "") -> str | None:
    """Return the registry token if the call receiver denotes a known client."""
    if not receiver:
        return None
    r = receiver.lower()
    for prefix in ("this.", "self."):
        if r.startswith(prefix):
            r = r[len(prefix):]
    for segment in r.split("."):
        if segment in _EXTERNAL_REGISTRY and not (language in _TYPED_LANGS and segment in _DYNAMIC_ONLY):
            return segment
    return None


def _extract_target(source: str, receiver: str, name: str) -> str:
    """Best-effort URL/path passed to an outbound HTTP call, from the caller's
    source. Enables cross-repo correlation (a consumer's outbound target matched
    against a provider's inbound endpoint). Returns "" when not a string literal.
    """
    if not source:
        return ""
    pat = re.escape(receiver) + r"\.\s*" + re.escape(name) + r"\s*\(\s*[`'\"]([^`'\"]+)[`'\"]"
    m = re.search(pat, source)
    return m.group(1) if m else ""


def extract_external_systems(nodes: list[CodeNode]) -> tuple[list[CodeNode], list[CallEdge]]:
    """Return (external_system nodes, external_call edges) synthesized from calls
    whose receiver matches a known client library."""
    systems: dict[str, CodeNode] = {}
    edges: list[CallEdge] = []
    seen_edges: set[tuple[str, str, str]] = set()

    for node in nodes:
        sites = node.call_sites or [{"name": c, "receiver": None} for c in node.calls]
        for site in sites:
            token = _match_external(site.get("receiver"), node.language)
            if not token:
                continue
            if token in _DYNAMIC_ONLY and token not in _GLOBALS and token not in _imported_names(node):
                continue   # `request` as a lambda parameter / local, not the library (M6.15)
            display, kind = _EXTERNAL_REGISTRY[token]
            qn = f"external::{token}"
            if qn not in systems:
                systems[qn] = CodeNode(
                    file_path="", language="external", node_type="external_system",
                    name=display, qualified_name=qn, source_code="",
                    line_start=0, line_end=0,
                    framework_hints={"system": display, "kind": kind, "client": token},
                )
            operation = site.get("name") or ""
            key = (node.qualified_name, qn, operation)
            if key in seen_edges:
                continue
            seen_edges.add(key)
            meta = {"client": token, "operation": operation}
            # For HTTP clients, capture the outbound target URL/path so the
            # cross-repo correlator can match it to a provider's inbound endpoint.
            if kind == "http_api":
                target = _extract_target(node.source_code, site.get("receiver") or "", operation)
                if target:
                    meta["target"] = target
            edge = make_edge(node.qualified_name, qn, "external_client", **meta)
            edge.edge_type = "external_call"
            edges.append(edge)
    return list(systems.values()), edges
