"""Page builder registry (roadmap item M3.1, ADR-0005 §3).

A builder is a pure function ``BuildContext -> Iterable[Page]`` declared with
``@page_builder`` in a module under ``ai_discovery.pages.builders``. Modules
there are discovered with ``pkgutil`` — adding a builder never edits a central
list. Each builder declares the page types it emits and the tables it reads;
emitting an undeclared type or a page id another builder already produced is
an error (no silent overwrite).
"""

from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from .model import PAGE_TYPES, Page, PageError, Stamp

BUILDERS_PACKAGE = "ai_discovery.pages.builders"


@dataclass(frozen=True)
class BuildContext:
    conn: Any                   # sqlite3.Connection (read)
    scan_id: int
    repo: Path
    config_hash: str = ""
    commit: str = ""
    dirty: bool = False
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Builder:
    name: str
    emits: frozenset[str]
    inputs: tuple[str, ...]
    version: str
    fn: Callable[[BuildContext], Iterable[Page]]

    def stamp(self, ctx: BuildContext) -> Stamp:
        return Stamp(builder=self.name, builder_version=self.version, config_hash=ctx.config_hash,
                     commit=ctx.commit, dirty=ctx.dirty)


_REGISTRY: dict[str, Builder] = {}


def page_builder(name: str, *, emits: Iterable[str], inputs: Iterable[str] = (), version: str = "1"):
    """Register the decorated function as a page builder."""
    emitted = frozenset(emits)
    unknown = emitted - PAGE_TYPES
    if unknown:
        raise PageError(f"builder {name!r} declares unknown page types {sorted(unknown)}")
    if not emitted:
        raise PageError(f"builder {name!r} must emit at least one page type")

    def deco(fn: Callable[[BuildContext], Iterable[Page]]):
        existing = _REGISTRY.get(name)
        if existing is not None and existing.fn is not fn:
            raise PageError(f"page builder {name!r} registered twice")
        _REGISTRY[name] = Builder(name, emitted, tuple(inputs), str(version), fn)
        return fn
    return deco


def discover_builders(package: str = BUILDERS_PACKAGE) -> list[Builder]:
    """Import every module in *package* (registering its builders); return all builders by name."""
    pkg = importlib.import_module(package)
    for info in pkgutil.iter_modules(pkg.__path__):
        if not info.name.startswith("_"):
            importlib.import_module(f"{package}.{info.name}")
    return [_REGISTRY[n] for n in sorted(_REGISTRY)]


def unregister(name: str) -> None:
    _REGISTRY.pop(name, None)


def run_builders(ctx: BuildContext, builders: Iterable[Builder] | None = None) -> list[Page]:
    """Run builders and return their pages sorted by id, stamped and validated."""
    pages: dict[str, tuple[str, Page]] = {}
    for b in (discover_builders() if builders is None else builders):
        stamp = b.stamp(ctx)
        for page in b.fn(ctx):
            if page.type not in b.emits:
                raise PageError(f"builder {b.name!r} emitted {page.type!r} page {page.page_id!r}, "
                                f"but declares {sorted(b.emits)}")
            if page.page_id in pages:
                raise PageError(f"page {page.page_id!r} produced by both {pages[page.page_id][0]!r} "
                                f"and {b.name!r}")
            if page.stamp is None:
                page.stamp = stamp
            pages[page.page_id] = (b.name, page)
    return [pages[k][1] for k in sorted(pages)]
