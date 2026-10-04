# ADR-0009 — Ship an MCP server as the agent interface

**Status:** accepted · **Date:** 2026-08-19

## Context
The README's stated consumer is "document-aware AI agents," yet v2's agent
story is "run a CLI, then walk a directory." The ecosystem converged on MCP
(docling-mcp, Playwright MCP); the official Python SDK is MIT and stable (v2
line).

## Decision
`webshot mcp` (stdio) built on the official `mcp` SDK, exposing capture,
read_markdown, query_chunks, get_manifest, list/read_asset, doctor — the
minimal set that lets an agent capture a URL and consume the result without
filesystem walking. Guardrails per spec §1.3/§6.8: allowlisted roots, no
credential-bearing parameters, auth profiles referenced by name only.

## Consequences
≈ +250 LOC net-new (accepted: it is a differentiator, not a reinvention —
nothing upstream exposes *capture* over MCP). REST facade deferred until MCP
usage data exists.
