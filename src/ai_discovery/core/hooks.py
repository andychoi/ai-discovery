"""Post-scan hook registry (roadmap item M2.3).

Work that runs after a successful scan (page build M3.5, FTS rebuild, …)
registers here instead of being wired into the pipeline. Hooks run in
registration order; a failing hook is reported as a degradation, never as a
failed scan.

Built-in hooks are listed in ``HOOK_MODULES`` (each module calls
``register_post_scan`` at import time); tests and plugins can call
``register_post_scan`` directly.
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Modules imported (once) before hooks run, so they can self-register.
HOOK_MODULES: tuple[str, ...] = ("ai_discovery.pages.build",)


@dataclass
class ScanContext:
    layout: Any                 # core.paths.ProjectLayout
    config: Any                 # config.DiscoveryConfig
    scan_id: int | None
    status: str | None
    extra: dict = field(default_factory=dict)


@dataclass
class HookResult:
    name: str
    ok: bool
    detail: str = ""


PostScanHook = Callable[[ScanContext], Any]
_HOOKS: list[tuple[str, PostScanHook]] = []
_loaded = False


def register_post_scan(name: str, fn: PostScanHook) -> None:
    if any(n == name for n, _ in _HOOKS):
        raise ValueError(f"post-scan hook {name!r} already registered")
    _HOOKS.append((name, fn))


def is_registered(name: str) -> bool:
    """Whether *name* is registered (does not import the built-in hook modules)."""
    return any(n == name for n, _ in _HOOKS)


def unregister_post_scan(name: str) -> None:
    _HOOKS[:] = [(n, f) for n, f in _HOOKS if n != name]


def registered_hooks() -> list[str]:
    _load_builtin()
    return [n for n, _ in _HOOKS]


def _load_builtin() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    for module in HOOK_MODULES:
        importlib.import_module(module)


def run_post_scan(ctx: ScanContext) -> list[HookResult]:
    """Run every hook; failures are captured, not raised."""
    _load_builtin()
    results: list[HookResult] = []
    for name, fn in list(_HOOKS):
        try:
            out = fn(ctx)
            results.append(HookResult(name, True, "" if out is None else str(out)))
        except Exception as exc:  # noqa: BLE001 — a hook failure is a degradation, not a scan failure
            logger.warning("post-scan hook %s failed: %s", name, exc)
            results.append(HookResult(name, False, f"{type(exc).__name__}: {exc}"))
    return results
