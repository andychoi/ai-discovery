"""Vue single-file components (roadmap item M6.5.b).

A `.vue` file is split without changing byte offsets: every byte outside the `<script>` /
`<script setup>` bodies becomes a space (newlines kept), so the JS/TS parser sees the scripts at their
real lines. The `<template>` block is scanned for the two bindings that make edges:

- event handlers — ``@submit.prevent="onSubmit"``, ``v-on:click="save(item)"`` → the handler names;
- component tags — ``<OrderForm />`` / ``<order-form>`` → the tag in PascalCase (matched against the
  script's imports by the resolver; unknown tags are HTML or globally registered and make no edge).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_SCRIPT = re.compile(rb"<script(?P<attrs>[^>]*)>(?P<body>.*?)</script\s*>", re.S | re.I)
_TEMPLATE_OPEN = re.compile(rb"<template(?:\s[^>]*)?>", re.I)
_TEMPLATE_ANY = re.compile(rb"<template(?:\s[^>]*)?>|</template\s*>", re.I)
_LANG = re.compile(rb"""\blang\s*=\s*["']?(\w+)""", re.I)
_HANDLER = re.compile(r"""(?:@|v-on:)[\w.:\-\[\]]+\s*=\s*(?:"([^"]*)"|'([^']*)')""")
_TAG = re.compile(r"<([A-Za-z][\w\-]*)")
_CALLED = re.compile(r"(?<![\w$.])([A-Za-z_][\w$]*)\s*\(")
_BARE = re.compile(r"^\s*([A-Za-z_][\w$]*)\s*$")
_NOT_HANDLERS = frozenset({"if", "for", "while", "switch", "return", "typeof", "new", "function"})


@dataclass
class SFC:
    script: bytes                     # same length as the source; only script bodies kept
    grammar: str = "js"               # js | ts | tsx (from `<script lang="…">`)
    template_line: int = 0            # 1-based line of `<template>`, 0 when there is none
    template_end: int = 0
    template: str = ""                # the `<template>` block's text
    handlers: list[tuple[str, int]] = field(default_factory=list)     # (function name, line)
    components: list[tuple[str, int]] = field(default_factory=list)   # (PascalCase tag, line)


def _blank(chunk: bytes) -> bytes:
    return bytes(b if b in (0x0A, 0x0D) else 0x20 for b in chunk)


def _line_of(source: bytes, offset: int) -> int:
    return source.count(b"\n", 0, offset) + 1


def _pascal(tag: str) -> str:
    return "".join(p[:1].upper() + p[1:] for p in tag.split("-")) if "-" in tag else tag


def _template_span(source: bytes) -> tuple[int, int] | None:
    """Byte span of the outermost `<template>` block (nested `<template v-if>` tags are counted)."""
    first = _TEMPLATE_OPEN.search(source)
    if first is None:
        return None
    depth = 0
    for m in _TEMPLATE_ANY.finditer(source, first.start()):
        depth += -1 if m.group(0).startswith(b"</") else 1
        if depth == 0:
            return first.start(), m.end()
    return first.start(), len(source)


def split_sfc(source: bytes) -> SFC:
    out = bytearray(_blank(source))
    grammar = "js"
    span = _template_span(source)
    for m in _SCRIPT.finditer(source):
        if span and span[0] <= m.start() < span[1]:
            continue   # a `<script>` inside the template is markup, not the component's code
        out[m.start("body"):m.end("body")] = m.group("body")
        lang = _LANG.search(m.group("attrs") or b"")
        if lang and lang.group(1).lower() in (b"ts", b"tsx"):
            grammar = lang.group(1).decode().lower()
    sfc = SFC(bytes(out), grammar)
    if span is None:
        return sfc
    sfc.template_line, sfc.template_end = _line_of(source, span[0]), _line_of(source, span[1])
    text = source[span[0]:span[1]].decode("utf-8", errors="replace")
    sfc.template = text
    base = sfc.template_line
    for m in _HANDLER.finditer(text):
        expr = m.group(1) if m.group(1) is not None else m.group(2)
        line = base + text.count("\n", 0, m.start())
        bare = _BARE.match(expr)
        names = [bare.group(1)] if bare else _CALLED.findall(expr)
        for name in dict.fromkeys(names):
            if name not in _NOT_HANDLERS:
                sfc.handlers.append((name, line))
    seen = set()
    for m in _TAG.finditer(text):
        tag = _pascal(m.group(1))
        if tag[:1].isupper() and tag != "Template" and tag not in seen:
            seen.add(tag)
            sfc.components.append((tag, base + text.count("\n", 0, m.start())))
    return sfc
