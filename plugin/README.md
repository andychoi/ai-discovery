# ai-discovery Claude Code plugin

Bundles the `ai-discovery` MCP server (`discover mcp serve`) and the skills that
use it. The `discover` CLI must be installed (`pip install ai-discovery`) and
the repository scanned (`discover scan`) before the tools return data.

## Install

```bash
claude plugin marketplace add ./plugin          # from a checkout of ai-discovery
claude plugin install ai-discovery@ai-discovery
```

The plugin starts the server in the session's working directory; `discover`
walks up to the nearest `.discovery/`. If `discover` is not on the `PATH`
Claude Code sees (e.g. it lives in a virtualenv), use
`discover mcp install --command "$(which discover)"` instead of the plugin.

Do not also run `discover mcp install --target claude` in the same project:
it detects the enabled plugin and refuses, so only one server is started.

## Skills

| Skill | Use it when | Writes |
|---|---|---|
| `discover-triage` | After a scan, before trusting it: verify low-confidence pages, guessed call edges and unresolved calls against the source | `.discovery/reports/triage-report.md` |
| `discover-consistency` | After triage or as an audit: dangling links, coverage gaps, missing expected flows (code gap vs discovery miss) | `.discovery/reports/consistency-report.md` |

Both skills are read-only with respect to the code and the index.
`discover-impact` and `discover-change-plan` arrive with roadmap items M5.5 and M5.6.

## Layout

```
plugin/
  .claude-plugin/plugin.json        manifest (name: ai-discovery)
  .claude-plugin/marketplace.json   single-plugin marketplace (source "./")
  .mcp.json                         the MCP server
  skills/<name>/SKILL.md
```

Validate with `claude plugin validate ./plugin`.
