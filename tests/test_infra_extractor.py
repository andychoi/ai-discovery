"""Tests for IaC (docker-compose / K8s) external-system ingestion (HIGH-7)."""

from pathlib import Path

from ai_discovery.extractors import read_infra_files
from ai_discovery.extractors.infra_extractor import _match_image


def test_match_image_strips_registry_and_tag():
    assert _match_image("postgres:15") == "postgres"
    assert _match_image("bitnami/kafka:latest") == "kafka"
    assert _match_image("confluentinc/cp-kafka:7.5.0") == "kafka"
    assert _match_image("redis:7-alpine") == "redis"
    assert _match_image("my-own-app:1.0") is None


def test_reads_docker_compose_backing_services():
    systems = read_infra_files(Path("tests/fixtures/projects/infra-compose"))
    by_kind = {n.framework_hints["kind"]: n.name for n in systems}
    assert by_kind == {"database": "PostgreSQL", "cache": "Redis", "queue": "Kafka"}
    assert all(n.framework_hints["source"] == "infra" for n in systems)
