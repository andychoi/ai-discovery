"""Tests for the import-map parser tier (assessment 07 A-6).

Lightweight regex extractors for Go/Rust/Ruby/PHP — NOT full tree-sitter
parsers. They extract imports (feeding Stage-2 import-scoped resolution and
semantic batching), top-level symbols with stem-based qualified names, and
conservative call sites. The tier converts "unsupported language" from a
cliff into a gradient; a deep parser can replace each later.
"""

from pathlib import Path

import pytest

from ai_discovery.parsers.import_map import (
    GoImportMapParser,
    PhpImportMapParser,
    RubyImportMapParser,
    RustImportMapParser,
)


def _parse(parser, tmp_path, filename: str, source: str):
    p = tmp_path / filename
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(source)
    return parser.parse_file(p)


def _by_qname(nodes):
    return {n.qualified_name: n for n in nodes}


# ---------------------------------------------------------------------------
# Go
# ---------------------------------------------------------------------------

GO_SRC = '''package orders

import (
	"fmt"
	"app/util"
	repo "app/storage"
)

type Order struct {
	ID int
}

type Notifier interface {
	Notify(msg string)
}

func CreateOrder(id int) *Order {
	util.Validate(id)
	repo.Save(id)
	fmt.Println("created")
	return &Order{ID: id}
}

func (o *Order) Total() int {
	return o.ID
}
'''


def test_go_imports_bind_last_segment_and_aliases(tmp_path):
    nodes = _parse(GoImportMapParser(), tmp_path, "orders.go", GO_SRC)
    imports = nodes[0].imports
    by_module = {i["module"]: i for i in imports}
    assert by_module["app.util"]["name"] == "util"      # plain import binds last segment
    assert by_module["app.storage"]["alias"] == "repo"  # aliased import binds alias
    assert by_module["fmt"]["name"] == "fmt"


def test_go_symbols_and_qualified_names(tmp_path):
    nodes = _by_qname(_parse(GoImportMapParser(), tmp_path, "orders.go", GO_SRC))
    assert nodes["orders.CreateOrder"].node_type == "function"
    assert nodes["orders.Order"].node_type == "class"
    assert nodes["orders.Notifier"].node_type == "class"
    assert nodes["orders.Order.Total"].node_type == "method"
    fn = nodes["orders.CreateOrder"]
    assert fn.language == "go"
    assert "util.Validate(id)" in fn.source_code     # brace-matched body captured
    assert fn.line_start < fn.line_end


def test_go_call_sites_with_receivers(tmp_path):
    nodes = _by_qname(_parse(GoImportMapParser(), tmp_path, "orders.go", GO_SRC))
    sites = nodes["orders.CreateOrder"].call_sites
    by_name = {(s.get("receiver"), s["name"]) for s in sites}
    assert ("util", "Validate") in by_name
    assert ("repo", "Save") in by_name
    assert ("fmt", "Println") in by_name
    # keywords/control flow never become calls
    assert not any(s["name"] in ("return", "if", "for", "func") for s in sites)


def test_go_framework_hints(tmp_path):
    src = 'package main\n\nimport "github.com/gin-gonic/gin"\n\nfunc main() {\n\tr := gin.Default()\n}\n'
    nodes = _parse(GoImportMapParser(), tmp_path, "main.go", src)
    assert any("gin" in (n.framework_hints.get("frameworks") or []) for n in nodes)


def test_go_can_parse():
    p = GoImportMapParser()
    assert p.language == "go"
    assert p.can_parse(Path("x/main.go"))
    assert not p.can_parse(Path("x/main.py"))


# ---------------------------------------------------------------------------
# Rust
# ---------------------------------------------------------------------------

RUST_SRC = '''use std::fmt;
use crate::storage::Repo;
use crate::pricing::{compute, Rate as R};

pub struct Order {
    pub id: u32,
}

impl Order {
    pub fn total(&self) -> u32 {
        compute(self.id);
        Repo::save(self.id);
        self.id
    }
}

pub fn create_order(id: u32) -> Order {
    Order { id }
}
'''


def test_rust_imports_normalized(tmp_path):
    nodes = _parse(RustImportMapParser(), tmp_path, "orders.rs", RUST_SRC)
    imports = nodes[0].imports
    entries = {(i["module"], i["name"], i["alias"]) for i in imports}
    assert ("std", "fmt", None) in entries
    assert ("crate.storage", "Repo", None) in entries
    assert ("crate.pricing", "compute", None) in entries   # brace group expanded
    assert ("crate.pricing", "Rate", "R") in entries        # alias captured


def test_rust_symbols_and_impl_methods(tmp_path):
    nodes = _by_qname(_parse(RustImportMapParser(), tmp_path, "orders.rs", RUST_SRC))
    assert nodes["orders.Order"].node_type == "class"
    assert nodes["orders.create_order"].node_type == "function"
    assert nodes["orders.Order.total"].node_type == "method"


def test_rust_call_sites(tmp_path):
    nodes = _by_qname(_parse(RustImportMapParser(), tmp_path, "orders.rs", RUST_SRC))
    sites = nodes["orders.Order.total"].call_sites
    pairs = {(s.get("receiver"), s["name"]) for s in sites}
    assert (None, "compute") in pairs
    assert ("Repo", "save") in pairs


# ---------------------------------------------------------------------------
# Ruby
# ---------------------------------------------------------------------------

RUBY_SRC = '''require 'json'
require_relative 'storage/repo'

class OrderService
  def create(id)
    Repo.save(id)
    notify(id)
  end

  def notify(id)
    puts id
  end
end

def standalone_helper
  JSON.parse("{}")
end
'''


def test_ruby_imports(tmp_path):
    nodes = _parse(RubyImportMapParser(), tmp_path, "order_service.rb", RUBY_SRC)
    modules = {i["module"] for i in nodes[0].imports}
    assert "json" in modules
    assert "storage.repo" in modules


def test_ruby_class_methods_and_top_level(tmp_path):
    nodes = _by_qname(_parse(RubyImportMapParser(), tmp_path, "order_service.rb", RUBY_SRC))
    assert nodes["order_service.OrderService"].node_type == "class"
    assert nodes["order_service.OrderService.create"].node_type == "method"
    assert nodes["order_service.OrderService.notify"].node_type == "method"
    assert nodes["order_service.standalone_helper"].node_type == "function"


def test_ruby_call_sites(tmp_path):
    nodes = _by_qname(_parse(RubyImportMapParser(), tmp_path, "order_service.rb", RUBY_SRC))
    sites = nodes["order_service.OrderService.create"].call_sites
    pairs = {(s.get("receiver"), s["name"]) for s in sites}
    assert ("Repo", "save") in pairs
    assert (None, "notify") in pairs


# ---------------------------------------------------------------------------
# PHP
# ---------------------------------------------------------------------------

PHP_SRC = '''<?php
namespace App\\Orders;

use App\\Storage\\Repo;
use App\\Pricing\\Calculator as Calc;

class OrderService
{
    public function create(int $id): void
    {
        Repo::save($id);
        $this->notify($id);
        Calc::rate($id);
    }

    private function notify(int $id): void
    {
    }
}

function standalone_helper()
{
    return 1;
}
'''


def test_php_imports_normalized(tmp_path):
    nodes = _parse(PhpImportMapParser(), tmp_path, "OrderService.php", PHP_SRC)
    entries = {(i["module"], i["name"], i["alias"]) for i in nodes[0].imports}
    assert ("App.Storage", "Repo", None) in entries
    assert ("App.Pricing", "Calculator", "Calc") in entries


def test_php_symbols_and_methods(tmp_path):
    nodes = _by_qname(_parse(PhpImportMapParser(), tmp_path, "OrderService.php", PHP_SRC))
    assert nodes["OrderService.OrderService"].node_type == "class"
    assert nodes["OrderService.OrderService.create"].node_type == "method"
    assert nodes["OrderService.standalone_helper"].node_type == "function"


def test_php_call_sites_arrow_and_static(tmp_path):
    nodes = _by_qname(_parse(PhpImportMapParser(), tmp_path, "OrderService.php", PHP_SRC))
    sites = nodes["OrderService.OrderService.create"].call_sites
    pairs = {(s.get("receiver"), s["name"]) for s in sites}
    assert ("Repo", "save") in pairs        # static ::
    assert ("Calc", "rate") in pairs
    assert ("this", "notify") in pairs       # $this-> arrow


# ---------------------------------------------------------------------------
# Shared behavior
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("parser_cls,filename", [
    (GoImportMapParser, "empty.go"),
    (RustImportMapParser, "empty.rs"),
    (RubyImportMapParser, "empty.rb"),
    (PhpImportMapParser, "empty.php"),
])
def test_empty_file_yields_no_nodes(parser_cls, filename, tmp_path):
    assert _parse(parser_cls(), tmp_path, filename, "\n") == []
