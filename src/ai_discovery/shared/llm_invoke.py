"""Shim (M1.10.a): the provider transport now lives in ``ai_discovery.llm.transport``.

This module *is* that module (aliased in ``sys.modules``), so existing imports
and test patches keep working. New code imports ``ai_discovery.llm.transport``.
"""

import sys

from ai_discovery.llm import transport as _transport

sys.modules[__name__] = _transport
