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


def _match_external(receiver: str | None) -> str | None:
    """Return the registry token if the call receiver denotes a known client."""
    if not receiver:
        return None
    r = receiver.lower()
    for prefix in ("this.", "self."):
        if r.startswith(prefix):
            r = r[len(prefix):]
    for segment in r.split("."):
        if segment in _EXTERNAL_REGISTRY:
            return segment
    return None


def extract_external_systems(nodes: list[CodeNode]) -> tuple[list[CodeNode], list[CallEdge]]:
    """Return (external_system nodes, external_call edges) synthesized from calls
    whose receiver matches a known client library."""
    systems: dict[str, CodeNode] = {}
    edges: list[CallEdge] = []
    seen_edges: set[tuple[str, str, str]] = set()

    for node in nodes:
        sites = node.call_sites or [{"name": c, "receiver": None} for c in node.calls]
        for site in sites:
            token = _match_external(site.get("receiver"))
            if not token:
                continue
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
            edges.append(CallEdge(
                caller=node.qualified_name, callee=qn,
                edge_type="external_call", confidence=0.9,
                metadata={"resolved_by": "external_system", "client": token, "operation": operation},
            ))
    return list(systems.values()), edges
