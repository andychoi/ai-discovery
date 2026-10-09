"""v1 commands — one module per command (roadmap M2).

Each module exposes ``register(app)``; keep heavy imports (pipeline,
tree-sitter, boto3, sqlite-vec) inside command bodies so ``--help`` stays
fast (tests/cli/test_cli_skeleton.py checks this).

Modules listed here are registered in order. Commands that still exist in the
legacy CLI (init, scan, ...) are added as their v1 items land (M2.2–M2.7).
"""

from __future__ import annotations

import importlib

import typer

V1_COMMANDS: tuple[str, ...] = ("status", "doctor")


def register(app: typer.Typer) -> None:
    for name in V1_COMMANDS:
        importlib.import_module(f"{__name__}.{name}").register(app)
