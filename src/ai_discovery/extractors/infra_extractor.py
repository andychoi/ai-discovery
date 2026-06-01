"""Infrastructure-as-code ingestion (HIGH-7).

Deployment descriptors declare the external systems an application depends on
(databases, caches, queues, search) even when the code-level client isn't
recognized. This reads `docker-compose.{yml,yaml}` and Kubernetes manifests and
promotes well-known service images to first-class `external_system` nodes — the
same node type code-level detection (HIGH-8) produces — so the deployment
topology's backing services are surfaced and queryable.

Conservative: only images/service names matching a registry of well-known
backing services promote; application images and generic sidecars are ignored.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..graph.models import CodeNode

logger = logging.getLogger(__name__)

# image / service-name token -> (display name, kind). Matched against the last
# path segment of an image (tag stripped) or the compose service name.
_IMAGE_REGISTRY: dict[str, tuple[str, str]] = {
    "postgres": ("PostgreSQL", "database"),
    "postgresql": ("PostgreSQL", "database"),
    "mysql": ("MySQL", "database"),
    "mariadb": ("MariaDB", "database"),
    "mongo": ("MongoDB", "database"),
    "mongodb": ("MongoDB", "database"),
    "cockroachdb": ("CockroachDB", "database"),
    "redis": ("Redis", "cache"),
    "memcached": ("Memcached", "cache"),
    "kafka": ("Kafka", "queue"),
    "rabbitmq": ("RabbitMQ", "queue"),
    "nats": ("NATS", "queue"),
    "elasticsearch": ("Elasticsearch", "search"),
    "opensearch": ("OpenSearch", "search"),
    "minio": ("MinIO", "storage"),
}


def _match_image(image_or_name: str) -> str | None:
    """Return the registry token for an image ref or service name, else None."""
    if not image_or_name:
        return None
    ref = image_or_name.strip().lower()
    ref = ref.split("@", 1)[0]            # drop digest
    last = ref.split("/")[-1]             # drop registry/namespace
    name = last.split(":", 1)[0]          # drop tag
    if name in _IMAGE_REGISTRY:
        return name
    # bitnami/kafka, confluentinc/cp-kafka, library/postgres → substring hit
    for token in _IMAGE_REGISTRY:
        if token in name:
            return token
    return None


def _node_for(token: str, source: str, declared_as: str) -> CodeNode:
    display, kind = _IMAGE_REGISTRY[token]
    return CodeNode(
        file_path=source, language="infra", node_type="external_system",
        name=display, qualified_name=f"external::{token}", source_code="",
        line_start=0, line_end=0,
        framework_hints={"system": display, "kind": kind, "client": token,
                         "source": "infra", "declared_as": declared_as},
    )


def read_infra_files(repo_path) -> list[CodeNode]:
    """Return external-system nodes declared in docker-compose / K8s manifests."""
    from ..repo.lang_detector import _SKIP_DIRS

    repo_path = Path(repo_path)
    systems: dict[str, CodeNode] = {}
    for path in sorted(repo_path.rglob("*")):
        if path.suffix.lower() not in (".yml", ".yaml") or not path.is_file():
            continue
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        try:
            if path.stat().st_size > 2_000_000:
                continue
            import yaml
            doc = yaml.safe_load(path.read_text(encoding="utf-8", errors="ignore"))
        except Exception as exc:
            logger.debug("Skipping unparseable infra candidate %s: %s", path, exc)
            continue
        if not isinstance(doc, dict):
            continue
        for token, declared in _scan_doc(doc, path.name):
            if token not in (s.framework_hints["client"] for s in systems.values()):
                systems[token] = _node_for(token, str(path), declared)
    return list(systems.values())


def _scan_doc(doc: dict, filename: str):
    """Yield (registry_token, declared_as) pairs from a compose or K8s document."""
    # docker-compose: services: {name: {image: ...}}
    services = doc.get("services")
    if isinstance(services, dict):
        for svc_name, svc in services.items():
            image = svc.get("image") if isinstance(svc, dict) else None
            token = _match_image(image) or _match_image(str(svc_name))
            if token:
                yield token, str(image or svc_name)
    # Kubernetes: spec.template.spec.containers[].image (Deployment/StatefulSet/Pod)
    containers = (
        doc.get("spec", {}).get("template", {}).get("spec", {}).get("containers")
        if isinstance(doc.get("spec"), dict) else None
    )
    if isinstance(containers, list):
        for c in containers:
            if isinstance(c, dict):
                token = _match_image(str(c.get("image", "")))
                if token:
                    yield token, str(c.get("image", ""))
