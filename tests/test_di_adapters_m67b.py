"""DI adapters (roadmap item M6.7.b): Spring @Bean and XML beans; single-type, factory, Unity, Autofac."""

from __future__ import annotations

from tests.test_di_registrations_m67a import _parse, _res

SENDERS = {
    "pom.xml": "<project/>",
    "src/main/java/n/Sender.java": "package n;\npublic interface Sender { void send(); }\n",
    "src/main/java/n/Sms.java": "package n;\npublic class Sms implements Sender { public void send() {} }\n",
    "src/main/java/n/Email.java": "package n;\npublic class Email implements Sender { public void send() {} }\n",
    "src/main/java/n/Push.java": "package n;\npublic class Push implements Sender { public void send() {} }\n",
}
USER = ("package n;\npublic class {cls} {{\n  private final Sender sender;\n"
        "  public {cls}({ann}Sender sender) {{ this.sender = sender; }}\n  public void go() {{ sender.send(); }}\n}}\n")


def test_bean_factory_method_and_qualifier_by_bean_name(tmp_path) -> None:
    edges, _ = _res(_parse(tmp_path, {**SENDERS,
        "src/main/java/n/Config.java": "package n;\n@Configuration\npublic class Config {\n"
                                       "  @Bean public Sender sender() { return new Sms(); }\n"
                                       "  @Bean(\"mailer\") public Sender mail() { return new Email(); }\n}\n",
        "src/main/java/n/Plain.java": USER.format(cls="Plain", ann=""),
        "src/main/java/n/Named.java": USER.format(cls="Named", ann="@Qualifier(\"mailer\") ")}))
    # two beans of type Sender, no qualifier, no @Primary: Spring would refuse — keep dispatching to both
    assert edges[("n.Plain.go", "n.Sms.send")] == "dispatch" and edges[("n.Plain.go", "n.Email.send")] == "dispatch"
    assert ("n.Plain.go", "n.Push.send") not in edges or edges[("n.Plain.go", "n.Push.send")] == "dispatch"
    assert edges[("n.Named.go", "n.Email.send")] == "framework"        # @Bean("mailer")
    assert ("n.Named.go", "n.Sms.send") not in edges


def test_single_bean_implementation_narrows_dispatch(tmp_path) -> None:
    edges, _ = _res(_parse(tmp_path, {**SENDERS,
        "src/main/java/n/Config.java": "package n;\n@Configuration\npublic class Config {\n"
                                       "  @Bean @Primary public Sender sender() { return new Push(); }\n}\n",
        "src/main/java/n/Plain.java": USER.format(cls="Plain", ann="")}))
    assert edges[("n.Plain.go", "n.Push.send")] == "framework"
    assert not [k for k in edges if k[0] == "n.Plain.go" and k[1] in ("n.Sms.send", "n.Email.send")]


def test_xml_beans(tmp_path) -> None:
    edges, _ = _res(_parse(tmp_path, {**SENDERS,
        "src/main/resources/beans.xml": '<beans>\n  <bean id="smsSender" class="n.Sms"/>\n</beans>\n',
        "src/main/java/n/Plain.java": USER.format(cls="Plain", ann=""),
        "src/main/java/n/Named.java": USER.format(cls="Named", ann="@Qualifier(\"smsSender\") ")}))
    assert edges[("n.Plain.go", "n.Sms.send")] == "framework"
    assert edges[("n.Named.go", "n.Sms.send")] == "framework"


CS = {
    "Shared/Shared.csproj": "<Project />",
    "Shared/Catalog.cs": "namespace Shared.Services;\npublic interface ICatalog { void Get(); }\n"
                         "public class CatalogService : ICatalog { public void Get() {} }\n",
    "Hybrid/Hybrid.csproj": "<Project />",
    "Hybrid/Catalog.cs": "namespace Hybrid.Services;\npublic class CatalogService : Shared.Services.ICatalog { public void Get() {} }\n",
    "Hybrid/Program.cs": "using Hybrid.Services;\nbuilder.Services.AddHttpClient<CatalogService>(o => o.X = 1);\n",
    "Web/Web.csproj": "<Project />",
    "Web/Extensions.cs": "using Shared.Services;\nnamespace Web;\npublic static class Extensions {\n"
                         "  public static void Add(IServiceCollection s) { s.AddHttpClient<CatalogService>(o => o.X = 1); }\n}\n",
    "Web/Chat.cs": "using Shared.Services;\nnamespace Web;\npublic class Chat {\n  private readonly ICatalog _catalog;\n"
                   "  public void Go() { _catalog.Get(); }\n}\n",
}


def test_single_type_registration_resolved_through_the_registering_files_usings(tmp_path) -> None:
    edges, _ = _res(_parse(tmp_path, CS))
    assert edges[("Web.Chat.Go", "Shared.Services.CatalogService.Get")] == "framework"
    assert ("Web.Chat.Go", "Hybrid.Services.CatalogService.Get") not in edges


def test_factory_unity_and_autofac_registrations(tmp_path) -> None:
    base = {"App/App.csproj": "<Project />",
            "App/Pay.cs": "namespace App;\npublic interface IPay { void Charge(); }\npublic interface INote { void Send(); }\n"
                          "public interface ILog { void Write(); }\n"
                          "public class Stripe : IPay { public void Charge() {} }\npublic class Fake : IPay { public void Charge() {} }\n"
                          "public class Mail : INote { public void Send() {} }\npublic class Sms : INote { public void Send() {} }\n"
                          "public class FileLog : ILog { public void Write() {} }\npublic class NullLog : ILog { public void Write() {} }\n"
                          "public class Use {\n  private readonly IPay _pay; private readonly INote _note; private readonly ILog _log;\n"
                          "  public void Go() { _pay.Charge(); _note.Send(); _log.Write(); }\n}\n",
            "App/Setup.cs": "namespace App;\npublic static class Setup {\n  public static void Add(IServiceCollection s, IUnityContainer c, ContainerBuilder b) {\n"
                            "    s.AddSingleton<IPay>(sp => new Stripe());\n    c.RegisterType<INote, Sms>();\n"
                            "    b.RegisterType<FileLog>().As<ILog>();\n  }\n}\n"}
    edges, _ = _res(_parse(tmp_path, base))
    assert edges[("App.Use.Go", "App.Stripe.Charge")] == "framework"      # factory lambda
    assert edges[("App.Use.Go", "App.Sms.Send")] == "framework"           # Unity
    assert edges[("App.Use.Go", "App.FileLog.Write")] == "framework"      # Autofac
    assert not {("App.Use.Go", t) for t in ("App.Fake.Charge", "App.Mail.Send", "App.NullLog.Write")} & set(edges)
