"""Shim (M1.10.a): the model catalog now lives in ``ai_discovery.llm.catalog``.

This module *is* that module (aliased in ``sys.modules``). New code imports
``ai_discovery.llm.catalog``.
"""

import sys

from ai_discovery.llm import catalog as _catalog

sys.modules[__name__] = _catalog
