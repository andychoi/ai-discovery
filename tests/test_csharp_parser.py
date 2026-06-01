from __future__ import annotations

from pathlib import Path

import pytest

from ai_discovery.parsers.csharp import CSharpParser


@pytest.fixture
def parser():
    return CSharpParser()


@pytest.fixture
def controller_file(tmp_path: Path):
    code = """\
// ASP.NET Controller
using Microsoft.AspNetCore.Mvc;
namespace PaymentApi.Controllers
{
    [ApiController]
    [Route("api/[controller]")]
    public class PaymentsController : ControllerBase
    {
        private readonly IPaymentService _service;
        public PaymentsController(IPaymentService service) { _service = service; }

        [HttpPost]
        public async Task<IActionResult> CreatePayment(PaymentRequest request)
        {
            var result = await _service.ProcessPayment(request);
            return Ok(result);
        }

        [HttpGet("{id}")]
        public async Task<IActionResult> GetPayment(string id)
        {
            return Ok(new { Id = id });
        }
    }
}
"""
    f = tmp_path / "PaymentsController.cs"
    f.write_text(code)
    return f


@pytest.fixture
def model_file(tmp_path: Path):
    code = """\
// EF Core Model
using System.ComponentModel.DataAnnotations.Schema;
namespace PaymentApi.Models
{
    [Table("payments")]
    public class Payment
    {
        public Guid Id { get; set; }
        public string CustomerId { get; set; }
        public decimal Amount { get; set; }
    }
}
"""
    f = tmp_path / "Payment.cs"
    f.write_text(code)
    return f


def test_extracts_controller_class(parser: CSharpParser, controller_file: Path):
    nodes = parser.parse_file(controller_file)
    classes = [n for n in nodes if n.node_type == "class"]
    assert len(classes) == 1
    assert classes[0].name == "PaymentsController"
    assert "ApiController" in classes[0].annotations
    assert classes[0].qualified_name == "PaymentApi.Controllers.PaymentsController"


def test_extracts_endpoints(parser: CSharpParser, controller_file: Path):
    nodes = parser.parse_file(controller_file)
    endpoints = [n for n in nodes if n.node_type == "endpoint"]
    assert len(endpoints) == 2

    by_name = {e.name: e for e in endpoints}

    # HIGH-4: routes compose the controller-level [Route("api/[controller]")]
    # prefix (with [controller] -> "Payments") rather than dropping it.
    create = by_name["CreatePayment"]
    assert create.framework_hints["method"] == "POST"
    assert create.framework_hints["route"] == "/api/Payments"

    get = by_name["GetPayment"]
    assert get.framework_hints["method"] == "GET"
    assert get.framework_hints["route"] == "/api/Payments/{id}"


def test_extracts_ef_model(parser: CSharpParser, model_file: Path):
    nodes = parser.parse_file(model_file)
    models = [n for n in nodes if n.node_type == "db_model"]
    assert len(models) == 1
    assert models[0].name == "Payment"
    assert models[0].framework_hints["table"] == "payments"
    assert models[0].qualified_name == "PaymentApi.Models.Payment"
    # Properties should be captured as params
    assert "Id" in models[0].params
    assert "CustomerId" in models[0].params
    assert "Amount" in models[0].params


def test_extracts_methods(parser: CSharpParser, controller_file: Path):
    nodes = parser.parse_file(controller_file)
    # Endpoints are not counted as regular methods
    methods = [n for n in nodes if n.node_type == "method"]
    assert len(methods) == 0  # All methods in this controller are endpoints


def test_constructor_di(parser: CSharpParser, controller_file: Path):
    nodes = parser.parse_file(controller_file)
    classes = [n for n in nodes if n.node_type == "class"]
    assert len(classes) == 1
    assert "IPaymentService" in classes[0].calls


def test_can_parse(parser: CSharpParser, tmp_path: Path):
    assert parser.can_parse(Path("Foo.cs"))
    assert not parser.can_parse(Path("bar.py"))
    assert not parser.can_parse(Path("baz.java"))


def test_endpoint_params(parser: CSharpParser, controller_file: Path):
    nodes = parser.parse_file(controller_file)
    endpoints = [n for n in nodes if n.node_type == "endpoint"]
    by_name = {e.name: e for e in endpoints}
    assert "request" in by_name["CreatePayment"].params
    assert "id" in by_name["GetPayment"].params


def test_extracts_field_types_for_di(parser: CSharpParser, tmp_path: Path):
    """HIGH-3: field + ctor-param types captured for receiver-type resolution."""
    code = (
        "namespace App {\n"
        "  public class Handler {\n"
        "    private readonly OrderService _svc;\n"
        "    public Handler(OrderService svc) { _svc = svc; }\n"
        "  }\n}\n"
    )
    f = tmp_path / "Handler.cs"; f.write_text(code)
    cls = next(n for n in parser.parse_file(f) if n.node_type == "class")
    ft = cls.framework_hints.get("field_types", {})
    assert ft.get("_svc") == "OrderService"
    assert ft.get("svc") == "OrderService"
