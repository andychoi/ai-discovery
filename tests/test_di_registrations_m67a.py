"""DI registrations (roadmap item M6.7.a): ASP.NET Add*<I,T>, Spring @Qualifier/@Primary, test doubles."""

from __future__ import annotations

from pathlib import Path

from ai_discovery.graph.call_graph import resolve_calls
from ai_discovery.parsers.csharp import CSharpParser
from ai_discovery.parsers.java import JavaParser


def _parse(root: Path, files: dict[str, str]) -> list:
    nodes = []
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    for p in sorted(root.rglob("*")):
        if p.suffix == ".java":
            nodes += JavaParser().parse_file(p)
        elif p.suffix == ".cs":
            nodes += CSharpParser().parse_file(p)
    return nodes


def _res(nodes):
    res = resolve_calls(nodes)
    return {(e.caller, e.callee): e.metadata.get("resolved_by") for e in res.edges}, res


CS = {
    "Api/Api.csproj": "<Project />",
    "Api/Pay.cs": "namespace Api;\npublic interface IPay { void Charge(); }\n"
                  "public class StripePay : IPay { public void Charge() {} }\n"
                  "public class FakePay : IPay { public void Charge() {} }\n"
                  "public interface INotify { void Send(); }\n"
                  "public class Mail : INotify { public void Send() {} }\n"
                  "public class Sms : INotify { public void Send() {} }\n"
                  "public class Checkout {\n  private readonly IPay _pay;\n  private readonly INotify _notify;\n"
                  "  public void Run() { _pay.Charge(); _notify.Send(); }\n}\n",
}


def test_aspnet_registration_in_top_level_program_picks_the_implementation(tmp_path) -> None:
    edges, _ = _res(_parse(tmp_path, {**CS, "Api/Program.cs": "using Api;\nvar b = WebApplication.CreateBuilder(args);\n"
                                                              "b.Services.AddScoped<IPay, StripePay>();\n"}))
    assert edges[("Api.Checkout.Run", "Api.StripePay.Charge")] == "framework"
    assert ("Api.Checkout.Run", "Api.FakePay.Charge") not in edges
    # INotify is not registered: dispatch to both implementations as before
    assert edges[("Api.Checkout.Run", "Api.Mail.Send")] == "dispatch"
    assert edges[("Api.Checkout.Run", "Api.Sms.Send")] == "dispatch"


def test_aspnet_registration_in_another_project_and_ambiguous_registrations(tmp_path) -> None:
    edges, res = _res(_parse(tmp_path, {**CS, "Lib/Lib.csproj": "<Project />",
                                        "Lib/Setup.cs": "namespace Lib;\npublic static class Setup {\n"
                                        "  public static void Add(IServiceCollection s) {\n"
                                        "    s.TryAddTransient<Api.IPay, Api.StripePay>();\n"
                                        "    s.AddSingleton<INotify, Mail>();\n    s.AddSingleton<INotify, Sms>();\n  }\n}\n"}))
    assert edges[("Api.Checkout.Run", "Api.StripePay.Charge")] == "framework"     # qualified generic args
    assert not [k for k in edges if k[0] == "Api.Checkout.Run" and k[1].endswith(".Send")]
    assert [u.reason for u in res.unresolved if u.callee_name == "Send"] == ["di_ambiguous"]


JAVA = {
    "a/Sender.java": "package a;\npublic interface Sender { void send(); }\n",
    "a/EmailSender.java": "package a;\nimport org.springframework.stereotype.Component;\n"
                          "@Component\npublic class EmailSender implements Sender { public void send() {} }\n",
    "a/PushSender.java": "package a;\nimport org.springframework.context.annotation.Primary;\n"
                         "import org.springframework.stereotype.Service;\n"
                         "@Primary\n@Service(\"push\")\npublic class PushSender implements Sender { public void send() {} }\n",
}


def test_spring_qualifier_on_field_and_constructor_and_default_bean_name(tmp_path) -> None:
    edges, _ = _res(_parse(tmp_path, {**JAVA,
        "a/ByField.java": "package a;\nimport org.springframework.beans.factory.annotation.Qualifier;\n"
                          "public class ByField {\n  @Autowired @Qualifier(\"emailSender\") private Sender sender;\n"
                          "  public void go() { sender.send(); }\n}\n",
        "a/ByCtor.java": "package a;\npublic class ByCtor {\n  private final Sender out;\n"
                         "  public ByCtor(@Qualifier(value = \"push\") Sender out) { this.out = out; }\n"
                         "  public void go() { this.out.send(); }\n}\n"}))
    assert edges[("a.ByField.go", "a.EmailSender.send")] == "framework"     # default name: emailSender
    assert ("a.ByField.go", "a.PushSender.send") not in edges
    assert edges[("a.ByCtor.go", "a.PushSender.send")] == "framework"
    assert ("a.ByCtor.go", "a.EmailSender.send") not in edges


def test_spring_primary_wins_without_a_qualifier(tmp_path) -> None:
    edges, _ = _res(_parse(tmp_path, {**JAVA, "a/Plain.java": "package a;\npublic class Plain {\n"
                                      "  private final Sender sender;\n  public Plain(Sender s) { this.sender = s; }\n"
                                      "  public void go() { sender.send(); }\n}\n"}))
    assert edges[("a.Plain.go", "a.PushSender.send")] == "framework"
    assert ("a.Plain.go", "a.EmailSender.send") not in edges


def test_test_doubles_never_reach_an_implementation(tmp_path) -> None:
    edges, res = _res(_parse(tmp_path, {**CS, **JAVA,
        "Api/CheckoutTests.cs": "namespace Api;\npublic class CheckoutTests {\n  private readonly IPay _payMock;\n"
                                "  public CheckoutTests() { _payMock = Substitute.For<IPay>(); }\n"
                                "  public void Charges() { _payMock.Charge(); }\n}\n",
        "a/SenderTest.java": "package a;\nimport org.mockito.Mock;\npublic class SenderTest {\n"
                             "  @Mock private Sender sender;\n  public void sends() { sender.send(); }\n"
                             "  public void local() { Sender s = mock(Sender.class); s.send(); }\n}\n"}))
    callers = ("Api.CheckoutTests.Charges", "a.SenderTest.sends", "a.SenderTest.local")
    # never an implementation class; a Java interface that declares the method is the static target itself
    assert {k for k in edges if k[0] in callers} == {("a.SenderTest.sends", "a.Sender.send"),
                                                      ("a.SenderTest.local", "a.Sender.send")}
    assert [u.reason for u in res.unresolved if u.callee_name == "Charge"
            and u.caller == "Api.CheckoutTests.Charges"] == ["external"]


def test_mock_of_a_spring_data_interface_still_targets_the_declared_method(tmp_path) -> None:
    edges, _ = _res(_parse(tmp_path, {
        "r/OwnerRepository.java": "package r;\npublic interface OwnerRepository { Object findById(int id); }\n",
        "r/OwnerTests.java": "package r;\npublic class OwnerTests {\n  @MockitoBean private OwnerRepository owners;\n"
                             "  public void setup() { owners.findById(1); }\n}\n"}))
    assert edges[("r.OwnerTests.setup", "r.OwnerRepository.findById")] == "receiver_typed"


def test_same_named_registered_classes_prefer_the_callers_project(tmp_path) -> None:
    edges, _ = _res(_parse(tmp_path, {
        "Shared/Shared.csproj": "<Project />",
        "Shared/IUrl.cs": "namespace Shared;\npublic interface IUrl { string Get(); }\n",
        "Web/Web.csproj": "<Project />",
        "Web/Url.cs": "namespace Web;\npublic class UrlProvider : Shared.IUrl { public string Get() { return \"\"; } }\n",
        "Web/Setup.cs": "namespace Web;\npublic static class Setup { public static void Add(IServiceCollection s) {"
                        " s.AddSingleton<IUrl, UrlProvider>(); } }\n",
        "Web/Chat.cs": "namespace Web;\npublic class Chat {\n  private readonly IUrl _url;\n  public void Go() { _url.Get(); }\n}\n",
        "Hybrid/Hybrid.csproj": "<Project />",
        "Hybrid/Url.cs": "namespace Hybrid;\npublic class UrlProvider : Shared.IUrl { public string Get() { return \"\"; } }\n",
        "Hybrid/Setup.cs": "namespace Hybrid;\npublic static class Setup { public static void Add(IServiceCollection s) {"
                           " s.AddSingleton<IUrl, UrlProvider>(); } }\n"}))
    assert edges[("Web.Chat.Go", "Web.UrlProvider.Get")] == "framework"
    assert ("Web.Chat.Go", "Hybrid.UrlProvider.Get") not in edges
