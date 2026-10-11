"""Benchmark harness (roadmap item M0.11, design/benchmark-protocol.md).

``python -m bench --help``. Repos are pinned in ``bench/repos.toml``; sources
come from a local checkout at the pinned SHA or are fetched into
``tests/corpus/external/.cache/`` (gitignored). Scans always write their state
to a temporary ``--state-dir`` — never into the scanned checkout.
"""
