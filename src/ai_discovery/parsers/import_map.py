"""Import-map parser tier — Go / Rust / Ruby / PHP (assessment 07 A-6).

Lightweight REGEX extractors, deliberately not tree-sitter parsers. The tier
converts "unsupported language" from a cliff into a gradient: it extracts

- **imports** with correct local bindings (feeding Stage-2 import-scoped call
  resolution and A-1 semantic batching) — module separators are normalized to
  dots (``a/b`` → ``a.b``, ``a::b`` → ``a.b``, ``A\\B`` → ``A.B``) so the
  resolver's Python-shaped matching works unchanged;
- **top-level symbols** (functions, methods, types) with the codebase's
  stem-based qualified names (``{stem}.{Name}``, ``{stem}.{Type}.{Method}``)
  and real line spans/bodies (feeding Tier-1 summarization, RAG, and domain
  classification);
- **conservative call sites** with receivers (``recv.method(`` / ``Recv::m(``
  / ``$this->m(``), keyword-filtered.

Known accepted bounds (this is the on-ramp, not the destination): regex body
matching can be confused by braces in strings/comments; Ruby nesting uses
indentation heuristics; no endpoint/entity extraction. A deep tree-sitter
parser can replace any of these per the extension checklist — nothing here
blocks that.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from ..graph.models import CodeNode
from .base import LanguageParser

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _brace_end(lines: list[str], start_idx: int) -> int:
    """Index of the line where the brace block opened at/after *start_idx*
    closes. Falls back to the declaration line when no block is found."""
    depth = 0
    started = False
    for i in range(start_idx, len(lines)):
        for ch in lines[i]:
            if ch == "{":
                depth += 1
                started = True
            elif ch == "}":
                depth -= 1
        if started and depth <= 0:
            return i
    return len(lines) - 1 if started else start_idx


def _ruby_end(lines: list[str], start_idx: int, indent: int) -> int:
    """Index of the ``end`` closing a Ruby def/class opened at *start_idx*."""
    for i in range(start_idx + 1, len(lines)):
        stripped = lines[i].strip()
        if stripped == "end" and (len(lines[i]) - len(lines[i].lstrip())) <= indent:
            return i
    return len(lines) - 1


def _extract_call_sites(
    body: str,
    receiver_re: re.Pattern,
    plain_re: re.Pattern,
    keywords: frozenset[str],
) -> list[dict]:
    """Conservative call-site extraction: receiver calls first, then plain
    calls (lookbehinds in *plain_re* keep them from re-matching the method
    part of receiver calls). Deduped, keyword-filtered."""
    sites: list[dict] = []
    seen: set[tuple] = set()

    for m in receiver_re.finditer(body):
        receiver = next(g for g in m.groups()[:-1] if g)
        name = m.groups()[-1]
        if name in keywords:
            continue
        key = (receiver, name)
        if key not in seen:
            seen.add(key)
            sites.append({"name": name, "receiver": receiver})

    for m in plain_re.finditer(body):
        name = m.group(1)
        if name in keywords:
            continue
        key = (None, name)
        if key not in seen:
            seen.add(key)
            sites.append({"name": name, "receiver": None})

    return sites


def _framework_hints(imports: list[dict], table: dict[str, tuple[str, ...]]) -> list[str]:
    modules = [i.get("module") or "" for i in imports]
    hits = [
        framework
        for framework, needles in table.items()
        if any(needle in module for needle in needles for module in modules)
    ]
    return hits


class _ImportMapParser(LanguageParser):
    """Shared scaffolding: read file → imports → symbols → CodeNodes.

    Subclasses implement `_imports(lines)` and `_symbols(lines, stem)`; the
    latter yields (qualified_name, name, node_type, start_idx, end_idx).
    """

    _FRAMEWORK_TABLE: dict[str, tuple[str, ...]] = {}
    _KEYWORDS: frozenset[str] = frozenset()
    _RECEIVER_RE: re.Pattern
    _PLAIN_RE: re.Pattern

    def parse_file(self, file_path: Path) -> list[CodeNode]:
        try:
            text = Path(file_path).read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            logger.warning("import-map: cannot read %s: %s", file_path, exc)
            return []
        if not text.strip():
            return []
        lines = text.splitlines()
        stem = Path(file_path).stem

        imports = self._imports(lines)
        frameworks = _framework_hints(imports, self._FRAMEWORK_TABLE)
        hints = {"frameworks": frameworks} if frameworks else {}

        nodes: list[CodeNode] = []
        for qualified, name, node_type, start, end in self._symbols(lines, stem):
            body = "\n".join(lines[start : end + 1])
            nodes.append(
                CodeNode(
                    file_path=str(file_path),
                    language=self.language,
                    node_type=node_type,
                    name=name,
                    qualified_name=qualified,
                    source_code=body,
                    line_start=start + 1,
                    line_end=end + 1,
                    imports=imports,
                    call_sites=_extract_call_sites(
                        body, self._RECEIVER_RE, self._PLAIN_RE, self._KEYWORDS
                    ),
                    calls=[],
                    framework_hints=dict(hints),
                )
            )
        return nodes

    def _imports(self, lines: list[str]) -> list[dict]:
        raise NotImplementedError

    def _symbols(self, lines: list[str], stem: str):
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Go
# ---------------------------------------------------------------------------

_GO_IMPORT_SINGLE = re.compile(r'^import\s+(?:(\w+|\.|_)\s+)?"([^"]+)"')
_GO_IMPORT_LINE = re.compile(r'^\s*(?:(\w+|\.|_)\s+)?"([^"]+)"')
_GO_FUNC = re.compile(r"^func\s+(\w+)\s*\(")
_GO_METHOD = re.compile(r"^func\s+\(\s*\w+\s+\*?(\w+)\s*\)\s*(\w+)\s*\(")
_GO_TYPE = re.compile(r"^type\s+(\w+)\s+(?:struct|interface)\b")


class GoImportMapParser(_ImportMapParser):
    _KEYWORDS = frozenset({
        "if", "for", "switch", "select", "return", "go", "defer", "range",
        "func", "make", "new", "len", "cap", "append", "copy", "delete",
        "panic", "recover", "close", "print", "println", "string", "int",
        "byte", "error",
    })
    _RECEIVER_RE = re.compile(r"\b(\w+)\.(\w+)\s*\(")
    _PLAIN_RE = re.compile(r"(?<![\w.])([A-Za-z_]\w*)\s*\(")
    _FRAMEWORK_TABLE = {
        "gin": ("gin-gonic.gin",),
        "echo": ("labstack.echo",),
        "net/http": ("net.http",),
        "gorm": ("gorm.io", "jinzhu.gorm"),
        "grpc": ("google.golang.org.grpc",),
    }

    @property
    def language(self) -> str:
        return "go"

    @property
    def extensions(self) -> frozenset[str]:
        return frozenset({".go"})

    @staticmethod
    def _import_record(alias: str | None, path: str) -> dict | None:
        if alias in ("_", "."):  # side-effect / dot imports bind nothing usable
            return None
        module = path.replace("/", ".")
        last = module.rsplit(".", 1)[-1]
        if alias:
            return {"module": module, "name": last, "alias": alias}
        # Go binds the LAST path segment (package name), not the first.
        return {"module": module, "name": last, "alias": None}

    def _imports(self, lines: list[str]) -> list[dict]:
        imports: list[dict] = []
        in_block = False
        for line in lines:
            stripped = line.strip()
            if in_block:
                if stripped.startswith(")"):
                    in_block = False
                    continue
                m = _GO_IMPORT_LINE.match(line)
                if m:
                    rec = self._import_record(m.group(1), m.group(2))
                    if rec:
                        imports.append(rec)
                continue
            if stripped.startswith("import ("):
                in_block = True
                continue
            m = _GO_IMPORT_SINGLE.match(stripped)
            if m:
                rec = self._import_record(m.group(1), m.group(2))
                if rec:
                    imports.append(rec)
        return imports

    def _symbols(self, lines: list[str], stem: str):
        for i, line in enumerate(lines):
            m = _GO_METHOD.match(line)
            if m:
                recv_type, name = m.group(1), m.group(2)
                yield f"{stem}.{recv_type}.{name}", name, "method", i, _brace_end(lines, i)
                continue
            m = _GO_FUNC.match(line)
            if m:
                yield f"{stem}.{m.group(1)}", m.group(1), "function", i, _brace_end(lines, i)
                continue
            m = _GO_TYPE.match(line)
            if m:
                yield f"{stem}.{m.group(1)}", m.group(1), "class", i, _brace_end(lines, i)


# ---------------------------------------------------------------------------
# Rust
# ---------------------------------------------------------------------------

_RUST_USE = re.compile(r"^\s*(?:pub\s+)?use\s+([\w:]+)(?:::\{([^}]+)\})?(?:\s+as\s+(\w+))?\s*;")
_RUST_FN = re.compile(r"^(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+(\w+)")
_RUST_TYPE = re.compile(r"^(?:pub(?:\([^)]*\))?\s+)?(?:struct|enum|trait)\s+(\w+)")
_RUST_IMPL = re.compile(r"^impl(?:<[^>]*>)?\s+(\w+)")
_RUST_IMPL_FN = re.compile(r"^\s+(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+(\w+)")


class RustImportMapParser(_ImportMapParser):
    _KEYWORDS = frozenset({
        "if", "for", "while", "loop", "match", "return", "fn", "let", "mut",
        "Some", "None", "Ok", "Err", "vec", "println", "print", "panic",
        "assert", "assert_eq", "Box", "String", "Vec",
    })
    _RECEIVER_RE = re.compile(r"\b(\w+)(?:::|\.)(\w+)\s*\(")
    _PLAIN_RE = re.compile(r"(?<![\w.:])([A-Za-z_]\w*)\s*\(")
    _FRAMEWORK_TABLE = {
        "actix": ("actix",),
        "axum": ("axum",),
        "rocket": ("rocket",),
        "tokio": ("tokio",),
        "diesel": ("diesel",),
    }

    @property
    def language(self) -> str:
        return "rust"

    @property
    def extensions(self) -> frozenset[str]:
        return frozenset({".rs"})

    def _imports(self, lines: list[str]) -> list[dict]:
        imports: list[dict] = []
        for line in lines:
            m = _RUST_USE.match(line)
            if not m:
                continue
            path, group, alias = m.group(1), m.group(2), m.group(3)
            dotted = path.replace("::", ".")
            if group:
                # use a::b::{C, D as E};
                for item in group.split(","):
                    item = item.strip()
                    if not item:
                        continue
                    if " as " in item:
                        name, item_alias = (s.strip() for s in item.split(" as ", 1))
                    else:
                        name, item_alias = item, None
                    imports.append({"module": dotted, "name": name, "alias": item_alias})
            else:
                module, _, name = dotted.rpartition(".")
                imports.append({
                    "module": module or dotted,
                    "name": name or dotted,
                    "alias": alias,
                })
        return imports

    def _symbols(self, lines: list[str], stem: str):
        i = 0
        while i < len(lines):
            line = lines[i]
            m = _RUST_IMPL.match(line)
            if m:
                impl_type = m.group(1)
                impl_end = _brace_end(lines, i)
                j = i + 1
                while j <= impl_end:
                    fm = _RUST_IMPL_FN.match(lines[j])
                    if fm:
                        fn_end = _brace_end(lines, j)
                        yield (
                            f"{stem}.{impl_type}.{fm.group(1)}", fm.group(1),
                            "method", j, fn_end,
                        )
                        j = fn_end + 1
                    else:
                        j += 1
                i = impl_end + 1
                continue
            m = _RUST_FN.match(line)
            if m:
                end = _brace_end(lines, i)
                yield f"{stem}.{m.group(1)}", m.group(1), "function", i, end
                i = end + 1
                continue
            m = _RUST_TYPE.match(line)
            if m:
                end = _brace_end(lines, i)
                yield f"{stem}.{m.group(1)}", m.group(1), "class", i, end
                i = end + 1
                continue
            i += 1


# ---------------------------------------------------------------------------
# Ruby
# ---------------------------------------------------------------------------

_RUBY_REQUIRE = re.compile(r"^\s*require(?:_relative)?\s+['\"]([^'\"]+)['\"]")
_RUBY_CLASS = re.compile(r"^(\s*)(?:class|module)\s+(\w+)")
_RUBY_DEF = re.compile(r"^(\s*)def\s+(?:self\.)?(\w+)")


class RubyImportMapParser(_ImportMapParser):
    _KEYWORDS = frozenset({
        "if", "unless", "while", "until", "case", "return", "def", "puts",
        "print", "raise", "require", "require_relative", "lambda", "proc",
        "attr_accessor", "attr_reader", "attr_writer", "new",
    })
    _RECEIVER_RE = re.compile(r"\b([A-Z]\w*(?:::\w+)*|\w+)\.(\w+)\s*\(")
    _PLAIN_RE = re.compile(r"(?<![\w.:])([a-z_]\w*)\s*\(")
    _FRAMEWORK_TABLE = {
        "rails": ("rails",),
        "sinatra": ("sinatra",),
        "sidekiq": ("sidekiq",),
        "rspec": ("rspec",),
    }

    @property
    def language(self) -> str:
        return "ruby"

    @property
    def extensions(self) -> frozenset[str]:
        return frozenset({".rb"})

    def _imports(self, lines: list[str]) -> list[dict]:
        return [
            {"module": m.group(1).replace("/", "."), "name": None, "alias": None}
            for line in lines
            if (m := _RUBY_REQUIRE.match(line))
        ]

    def _symbols(self, lines: list[str], stem: str):
        # Indentation-based class tracking (idiomatic-Ruby heuristic): a def
        # indented deeper than the most recent open class belongs to it.
        class_stack: list[tuple[int, str, int]] = []  # (indent, name, end_idx)
        for i, line in enumerate(lines):
            while class_stack and i > class_stack[-1][2]:
                class_stack.pop()
            m = _RUBY_CLASS.match(line)
            if m:
                indent = len(m.group(1))
                end = _ruby_end(lines, i, indent)
                yield f"{stem}.{m.group(2)}", m.group(2), "class", i, end
                class_stack.append((indent, m.group(2), end))
                continue
            m = _RUBY_DEF.match(line)
            if m:
                indent = len(m.group(1))
                end = _ruby_end(lines, i, indent)
                if class_stack and indent > class_stack[-1][0]:
                    owner = class_stack[-1][1]
                    yield f"{stem}.{owner}.{m.group(2)}", m.group(2), "method", i, end
                else:
                    yield f"{stem}.{m.group(2)}", m.group(2), "function", i, end


# ---------------------------------------------------------------------------
# PHP
# ---------------------------------------------------------------------------

_PHP_USE = re.compile(r"^\s*use\s+([\w\\]+)(?:\s+as\s+(\w+))?\s*;")
_PHP_CLASS = re.compile(r"^\s*(?:final\s+|abstract\s+)?(?:class|interface|trait)\s+(\w+)")
_PHP_METHOD = re.compile(
    r"^\s+(?:public\s+|private\s+|protected\s+|static\s+|final\s+|abstract\s+)*function\s+(\w+)\s*\("
)
_PHP_FUNCTION = re.compile(r"^function\s+(\w+)\s*\(")


class PhpImportMapParser(_ImportMapParser):
    _KEYWORDS = frozenset({
        "if", "for", "foreach", "while", "switch", "return", "function",
        "echo", "print", "isset", "unset", "empty", "array", "list", "new",
        "require", "require_once", "include", "include_once", "die", "exit",
    })
    _RECEIVER_RE = re.compile(r"(?:\$(\w+)\s*->|(\w+)\s*::)\s*(\w+)\s*\(")
    _PLAIN_RE = re.compile(r"(?<![\w$>:])([A-Za-z_]\w*)\s*\(")
    _FRAMEWORK_TABLE = {
        "laravel": ("Illuminate.",),
        "symfony": ("Symfony.",),
        "wordpress": ("WP_",),
    }

    @property
    def language(self) -> str:
        return "php"

    @property
    def extensions(self) -> frozenset[str]:
        return frozenset({".php"})

    def _imports(self, lines: list[str]) -> list[dict]:
        imports: list[dict] = []
        for line in lines:
            m = _PHP_USE.match(line)
            if not m:
                continue
            dotted = m.group(1).replace("\\", ".")
            module, _, name = dotted.rpartition(".")
            imports.append({
                "module": module or dotted,
                "name": name or dotted,
                "alias": m.group(2),
            })
        return imports

    def _symbols(self, lines: list[str], stem: str):
        i = 0
        while i < len(lines):
            line = lines[i]
            m = _PHP_CLASS.match(line)
            if m:
                class_name = m.group(1)
                class_end = _brace_end(lines, i)
                yield f"{stem}.{class_name}", class_name, "class", i, class_end
                j = i + 1
                while j <= class_end:
                    fm = _PHP_METHOD.match(lines[j])
                    if fm:
                        fn_end = _brace_end(lines, j)
                        yield (
                            f"{stem}.{class_name}.{fm.group(1)}", fm.group(1),
                            "method", j, fn_end,
                        )
                        j = fn_end + 1
                    else:
                        j += 1
                i = class_end + 1
                continue
            m = _PHP_FUNCTION.match(line)
            if m:
                end = _brace_end(lines, i)
                yield f"{stem}.{m.group(1)}", m.group(1), "function", i, end
                i = end + 1
                continue
            i += 1


IMPORT_MAP_PARSERS = (
    GoImportMapParser,
    RustImportMapParser,
    RubyImportMapParser,
    PhpImportMapParser,
)
