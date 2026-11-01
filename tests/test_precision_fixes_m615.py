"""Precision fixes from the M6.14 re-grade (roadmap item M6.15)."""

from __future__ import annotations

from pathlib import Path

from ai_discovery.extractors.external_system_extractor import _match_external
from ai_discovery.extractors.proto_extractor import read_proto_files
from ai_discovery.graph.call_graph import resolve_calls
from ai_discovery.parsers.csharp import CSharpParser
from ai_discovery.parsers.java import JavaParser


def _java(root: Path, files: dict[str, str]) -> list:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return [n for f in sorted(root.rglob("*.java")) for n in JavaParser().parse_file(f)]


VALIDATOR = "package c;\nimport org.springframework.validation.Validator;\n" \
            "public class PetValidator implements Validator {\n  public void validate(Object o, Object e) {}\n}\n"


def test_implementation_of_a_different_same_named_interface_is_not_linked(tmp_path) -> None:
    nodes = _java(tmp_path, {
        "c/PetValidator.java": VALIDATOR,
        "c/JakartaTest.java": "package c;\nimport jakarta.validation.Validator;\npublic class JakartaTest {\n"
                              "  private Validator validator;\n  public void run() { validator.validate(1); }\n}\n",
        "c/SpringUse.java": "package c;\nimport org.springframework.validation.Validator;\npublic class SpringUse {\n"
                            "  private Validator validator;\n  public void run() { validator.validate(1, 2); }\n}\n"})
    edges = {(e.caller, e.callee) for e in resolve_calls(nodes).edges}
    assert ("c.JakartaTest.run", "c.PetValidator.validate") not in edges
    assert ("c.SpringUse.run", "c.PetValidator.validate") in edges     # same interface: still dispatched


def test_enhanced_for_variable_typed_by_a_library_type_is_external(tmp_path) -> None:
    nodes = _java(tmp_path, {
        "m/NamedEntity.java": "package m;\npublic class NamedEntity { public String getName() { return \"\"; } }\n",
        "m/Logger.java": "package m;\nimport org.springframework.core.env.PropertySource;\nimport java.util.List;\n"
                         "public class Logger {\n  public void print(List<PropertySource<?>> sources) {\n"
                         "    for (PropertySource<?> source : sources) { source.getName(); }\n  }\n}\n"})
    res = resolve_calls(nodes)
    assert not [e for e in res.edges if e.caller == "m.Logger.print" and e.callee == "m.NamedEntity.getName"]
    assert [u.reason for u in res.unresolved if u.caller == "m.Logger.print" and u.callee_name == "getName"] == ["external"]


def test_dynamic_language_client_names_do_not_match_in_typed_languages() -> None:
    assert _match_external("request", "csharp") is None            # a MediatR command / DTO variable
    assert _match_external("requests", "java") is None
    assert _match_external("request", "javascript") == "request"   # the `request` npm library
    assert _match_external("httpClient", "csharp") == "httpclient"


def test_duplicate_proto_messages_qualified_and_bound_to_the_callers_copy(tmp_path) -> None:
    for d, ns in (("Server/Proto", "Shop.Server.Grpc"), ("Client/Protos", "Shop.Client.Grpc")):
        (tmp_path / d).mkdir(parents=True)
        (tmp_path / d / "basket.proto").write_text(
            f'syntax = "proto3";\noption csharp_namespace = "{ns}";\npackage BasketApi;\n'
            "message BasketReply {\n  string id = 1;\n}\n")
    (tmp_path / "Client/Protos/Basket.cs").write_text(
        "namespace Shop.Client.Grpc;\npublic class Mapper {\n  public Mapper(BasketReply reply) { }\n}\n")
    protos = read_proto_files(tmp_path)
    assert sorted(p.qualified_name for p in protos) == ["proto::Shop.Client.Grpc.BasketReply",
                                                        "proto::Shop.Server.Grpc.BasketReply"]
    nodes = CSharpParser().parse_file(tmp_path / "Client/Protos/Basket.cs") + protos
    edges = {(e.caller, e.callee): e.metadata.get("resolved_by") for e in resolve_calls(nodes).edges}
    assert edges[("Shop.Client.Grpc.Mapper", "proto::Shop.Client.Grpc.BasketReply")] == "receiver_typed"
    assert ("Shop.Client.Grpc.Mapper", "proto::Shop.Server.Grpc.BasketReply") not in edges


def test_a_single_proto_keeps_its_short_name(tmp_path) -> None:
    (tmp_path / "a.proto").write_text('syntax = "proto3";\noption csharp_namespace = "X";\nmessage Only {\n  int32 a = 1;\n}\n')
    assert [p.qualified_name for p in read_proto_files(tmp_path)] == ["proto::Only"]


def test_type_parameter_locals_use_the_bound_or_nothing(tmp_path) -> None:
    nodes = _java(tmp_path, {
        "m/BaseEntity.java": "package m;\npublic class BaseEntity { public Integer getId() { return 1; } }\n",
        "m/Utils.java": "package m;\nimport java.util.Collection;\npublic class Utils {\n"
                        "  public static <T extends BaseEntity> T byId(Collection<T> es, int id) {\n"
                        "    for (T e : es) { if (e.getId() == id) return e; }\n    return null;\n  }\n"
                        "  public static <E> void each(Collection<E> es) { for (E e : es) { e.getId(); } }\n}\n"})
    res = resolve_calls(nodes)
    edges = {(e.caller, e.callee): e.metadata.get("resolved_by") for e in res.edges}
    assert edges[("m.Utils.byId", "m.BaseEntity.getId")] == "receiver_typed"      # bound of T
    assert "external" not in [u.reason for u in res.unresolved if u.caller == "m.Utils.each"]


def test_dynamic_client_token_needs_an_import(tmp_path) -> None:
    from ai_discovery.extractors.external_system_extractor import extract_external_systems
    from ai_discovery.parsers.javascript import JavaScriptParser

    (tmp_path / "e2e.ts").write_text("export async function wait(page: any) {\n"
                                      "  return page.waitForRequest((request: any) => request.method() === 'POST');\n}\n")
    (tmp_path / "api.js").write_text("const request = require('request');\nfunction load() { request.get('/x'); }\n")
    nodes = [n for f in ("e2e.ts", "api.js") for n in JavaScriptParser().parse_file(tmp_path / f)]
    _, edges = extract_external_systems(nodes)
    assert {(e.caller, e.callee) for e in edges} == {("api.load", "external::request")}
