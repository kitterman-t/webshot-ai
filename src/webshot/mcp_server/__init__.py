"""The MCP server (ADR-0009): WebShot's agent-facing interface.

Five modules rather than the single `mcp_server.py` the module map budgeted
(docs/09 P4-2, P4-9), and the split is what makes the bridge rule mean
something:

* `policy.py` — the refusals, and the resolved boundary. Imports nothing
  upstream, so every guardrail is testable as a plain function call.
* `service.py` — what the tools do: capture, stage, publish, read a bundle back.
  Protocol-free, so it is testable without `webshot[mcp]` installed.
* `results.py` — the published result models.
* `server.py` — the SDK bridge, and the only module that imports `mcp`.
* `cli.py` — the `webshot mcp` subcommand, which loads the SDK lazily.
"""
