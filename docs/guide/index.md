# WebShot

WebShot turns a web page or a local document into two things at once: a
**polished, tagged PDF** a person can read, and an **AI-ready bundle** an agent
can consume — markdown, chunk records with provenance, tables as CSV, the page's
visual assets with their recognized text, and a manifest that says exactly what
was captured, when, and with which tools.

It is a capture tool, which means two properties matter more here than they do in
most software: **output does not change by accident**, and **credentials never
end up in a deliverable**. Nearly everything in these pages exists to protect one
of those two.

<div class="grid cards" markdown>

-   **Start here**

    [Quickstart](quickstart.md) — install, capture a page, read what came out.

-   **Everything the CLI does**

    [CLI reference](cli.md) — generated from the parser, so it cannot drift.

-   **What lands on disk**

    [Bundle format 3](bundle-format.md) — the files, the manifest, the schemas.

-   **For agents**

    [MCP server](mcp.md) — the tool contract, and the rules it runs under.

-   **Settings**

    [Configuration](configuration.md) — `webshot.toml`, `WEBSHOT_*`, precedence.

-   **Behind a login**

    [Signed-in and protected](signed-in.md) — storage state, browser profiles,
    protected viewers, PDF/A.

-   **When something is wrong**

    [Troubleshooting](troubleshooting.md) — `webshot doctor`, exit codes,
    degraded captures.

</div>

## What WebShot is for

An archive of a page you can still read in five years, and an extraction of that
page an agent can reason over without scraping it again. The PDF and the bundle
describe the same capture: the manifest records the source URL, the final URL
after redirects, the capture time, SHA-256 of every file, and the versions of
every tool involved, so a downstream claim about the page can be audited back to
the capture that made it.

## What WebShot is not

- **Not a crawler.** One invocation fetches one page (plus that page's own
  subresources, as any browser does). See the
  [capture ethics policy](../11-capture-ethics.md).
- **Not a way around access control.** The protected-viewer path captures only
  what an authorized viewer already rendered for a signed-in user. It never
  requests the protected native file and never reads credentials. It does make
  a copy of what you can see, the way printing each page would, so if the
  document's owner has blocked downloading or copying, treat that block as
  covering WebShot too. Features that would bypass access controls, DRM, IRM,
  or CAPTCHAs are on a permanent never-list.
- **Not a PII scrubber.** Captured pages may contain personal data; the manifest
  records provenance so retention policy can be applied by the operator. A
  redaction pass is deliberately out of scope for v3 — it would create false
  confidence.

## Security in one paragraph

Storage-state files and authentication profiles are never copied into a bundle
or a PDF. `type=password` values are redacted from every semantic artifact.
Sanitized HTML keeps no `<script>`, no event handlers, and no URL scheme outside
`http(s)`/`data`/`mailto`. All XML goes through `defusedxml`. The MCP server —
the agent-facing surface, and the one an attacker can reach through a captured
page — takes no credential-bearing parameters, reads only inside configured
roots, publishes only under its own output root, and refuses private, loopback,
and link-local targets by default. The whole model is in
[Security](security.md).

## Status

WebShot v3 is a rebuild of v2.2 as a composition of open-source components.
If you are upgrading, read the [migration guide](../migration-v2-to-v3.md) — exit
codes changed deliberately, and the bundle format is at version 3.
