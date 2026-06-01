"""Tests for OpenAPI/Swagger ingestion (HIGH-7)."""

from pathlib import Path

from ai_discovery.extractors import extract_openapi_endpoints, read_openapi_files


def test_extract_openapi3_with_server_prefix():
    spec = {
        "openapi": "3.0.0",
        "servers": [{"url": "https://api.example.com/api/v1"}],
        "paths": {
            "/pets": {"get": {"operationId": "listPets"}, "post": {"operationId": "createPet"}},
            "/pets/{id}": {"get": {"operationId": "getPet"}},
        },
    }
    eps = {(n.framework_hints["method"], n.framework_hints["route"]) for n in extract_openapi_endpoints(spec)}
    assert ("GET", "/api/v1/pets") in eps
    assert ("POST", "/api/v1/pets") in eps
    assert ("GET", "/api/v1/pets/{id}") in eps


def test_swagger2_basepath():
    spec = {"swagger": "2.0", "basePath": "/v2", "paths": {"/orders": {"get": {}}}}
    eps = {(n.framework_hints["method"], n.framework_hints["route"]) for n in extract_openapi_endpoints(spec)}
    assert ("GET", "/v2/orders") in eps


def test_non_spec_dict_yields_nothing():
    assert extract_openapi_endpoints({"foo": "bar"}) == []
    assert extract_openapi_endpoints({"paths": "not-a-dict"}) == []


def test_reads_yaml_spec_from_fixture():
    nodes = read_openapi_files(Path("tests/fixtures/projects/openapi-spec"))
    routes = {(n.framework_hints["method"], n.framework_hints["route"]) for n in nodes}
    assert ("DELETE", "/api/v1/pets/{petId}") in routes
    assert all(n.framework_hints["source"] == "openapi" for n in nodes)
