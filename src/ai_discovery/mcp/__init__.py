"""MCP server for coding agents (roadmap item M5.3, design/mcp-and-agents.md).

- ``tool.py`` — ``ToolSpec`` + ``Budget`` (SDK-free);
- ``tools/`` — one module per tool family, auto-discovered;
- ``registry.py`` — discovery, profiles (default / lean / all) and ``--tools`` selection;
- ``middleware.py`` — shield → handler → budget → meta, never raises;
- ``server.py`` — adapter to the official ``mcp`` SDK (stdio).

The SDK is imported only by ``server.py`` so the rest stays importable without it.
"""
