from __future__ import annotations

from pathlib import Path

import pytest

from app.parsers.java import JavaParser


@pytest.fixture
def parser():
    return JavaParser()


@pytest.fixture
def sample_controller(tmp_path: Path):
    code = """\
package com.corp.payments;

import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/payments")
public class PaymentController {
    private final PaymentService service;

    @Autowired
    public PaymentController(PaymentService service) {
        this.service = service;
    }

    @PostMapping
    public Payment createPayment(@RequestBody PaymentRequest req) {
        return service.process(req);
    }

    @GetMapping("/{id}")
    public Payment getPayment(@PathVariable String id) {
        return service.findById(id);
    }
}
"""
    f = tmp_path / "PaymentController.java"
    f.write_text(code)
    return f


@pytest.fixture
def sample_entity(tmp_path: Path):
    code = """\
package com.corp.payments;

import javax.persistence.*;

@Entity
@Table(name = "payments")
public class Payment {
    @Id
    private UUID id;
    private String customerId;
    private BigDecimal amount;
}
"""
    f = tmp_path / "Payment.java"
    f.write_text(code)
    return f


@pytest.fixture
def sample_batch(tmp_path: Path):
    code = """\
package com.corp.jobs;

import org.springframework.scheduling.annotation.Scheduled;

public class ReportJob {
    @Scheduled(cron = "0 0 2 * * ?")
    public void generateDailyReport() {
        buildReport();
    }
}
"""
    f = tmp_path / "ReportJob.java"
    f.write_text(code)
    return f


def test_extracts_controller(parser: JavaParser, sample_controller: Path):
    nodes = parser.parse_file(sample_controller)
    classes = [n for n in nodes if n.node_type == "class"]
    assert len(classes) == 1
    assert classes[0].name == "PaymentController"
    assert "RestController" in classes[0].annotations
    assert classes[0].qualified_name == "com.corp.payments.PaymentController"


def test_extracts_endpoints(parser: JavaParser, sample_controller: Path):
    nodes = parser.parse_file(sample_controller)
    endpoints = [n for n in nodes if n.node_type == "endpoint"]
    assert len(endpoints) == 2

    by_name = {e.name: e for e in endpoints}

    create = by_name["createPayment"]
    assert create.framework_hints["method"] == "POST"
    assert create.framework_hints["route"] == "/api/payments"

    get = by_name["getPayment"]
    assert get.framework_hints["method"] == "GET"
    assert get.framework_hints["route"] == "/api/payments/{id}"


def test_extracts_entity(parser: JavaParser, sample_entity: Path):
    nodes = parser.parse_file(sample_entity)
    db_models = [n for n in nodes if n.node_type == "db_model"]
    assert len(db_models) == 1
    assert db_models[0].name == "Payment"
    assert db_models[0].framework_hints.get("table") == "payments"


def test_extracts_batch_job(parser: JavaParser, sample_batch: Path):
    nodes = parser.parse_file(sample_batch)
    jobs = [n for n in nodes if n.node_type == "batch_job"]
    assert len(jobs) == 1
    assert jobs[0].name == "generateDailyReport"


def test_autowired_di_references(parser: JavaParser, sample_controller: Path):
    nodes = parser.parse_file(sample_controller)
    cls = next(n for n in nodes if n.node_type == "class")
    assert "PaymentService" in cls.calls


def test_can_parse(parser: JavaParser, tmp_path: Path):
    assert parser.can_parse(Path("Foo.java"))
    assert not parser.can_parse(Path("foo.py"))
    assert not parser.can_parse(Path("foo.js"))


def test_package_qualified_name(parser: JavaParser, sample_controller: Path):
    nodes = parser.parse_file(sample_controller)
    cls = next(n for n in nodes if n.node_type == "class")
    assert cls.qualified_name == "com.corp.payments.PaymentController"


def test_method_params(parser: JavaParser, sample_controller: Path):
    nodes = parser.parse_file(sample_controller)
    create = next(n for n in nodes if n.name == "createPayment")
    assert "req" in create.params


def test_return_types(parser: JavaParser, sample_controller: Path):
    nodes = parser.parse_file(sample_controller)
    create = next(n for n in nodes if n.name == "createPayment")
    assert create.return_type == "Payment"
