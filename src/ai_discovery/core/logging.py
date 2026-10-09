"""One-time logging setup for the CLI (roadmap item M2.1).

Levels: ``-q`` → ERROR, default → WARNING, ``-v`` → INFO, ``-vv`` → DEBUG.
Logs go to stderr (stdout is reserved for ``--json`` results) and, when a log
file is given, to that file at DEBUG. Python ``warnings`` are routed through
logging so nothing prints raw.
"""

from __future__ import annotations

import logging
from pathlib import Path

_HANDLER_TAG = "_ai_discovery_cli"


def level_for(verbosity: int, quiet: bool) -> int:
    if quiet:
        return logging.ERROR
    return {0: logging.WARNING, 1: logging.INFO}.get(verbosity, logging.DEBUG)


def configure_logging(verbosity: int = 0, quiet: bool = False,
                      log_file: Path | None = None) -> None:
    """Configure the root logger once; later calls only adjust levels/handlers."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, _HANDLER_TAG, False):
            root.removeHandler(handler)
            handler.close()

    level = level_for(verbosity, quiet)
    stderr = logging.StreamHandler()
    stderr.setLevel(level)
    stderr.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    setattr(stderr, _HANDLER_TAG, True)
    root.addHandler(stderr)
    root.setLevel(min(level, logging.DEBUG if log_file else level))

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        setattr(fh, _HANDLER_TAG, True)
        root.addHandler(fh)

    # Reset first: captureWarnings(True) is a no-op if logging believes it
    # already captured, even when something has since restored showwarning.
    logging.captureWarnings(False)
    logging.captureWarnings(True)
