# Migrating a bundle consumer from v2 to v3

WebShot v3 changes the **web-path** AI bundle from WebShot's bespoke block
format to `bundle_format: 3`, whose `content.json` is a lossless
[DoclingDocument](https://github.com/docling-project/docling-core). The
protected-viewer bundle is **unchanged** (`schema_version: 1.1`), and so is
every PDF deliverable. This page is for code that reads the bundle.

## Detecting the format

Read `manifest.json` first — every bundle self-describes:

| You find | You have |
|---|---|
| `"bundle_format": 3` | a v3 web bundle (this page) |
| `"schema_version": "1.0"` | a v2.2 web bundle (frozen schemas: `schemas/legacy-*.schema.json`) |
| `"schema_version": "1.1"` | a protected-viewer bundle (unchanged) |

## What changed, file by file

| File | v2.2 | v3 |
|---|---|---|
| `manifest.json` | `schema_version: "1.0"` | `bundle_format: 3`; adds `auth_mode`, `tool_versions` (the exact upstream versions that produced the bundle), and `page_metadata` — the DOM-declared description/author/keywords/canonicalUrl/publishedTime that v2 kept inside `content.json.metadata`; `content.blocks` becomes `content.items` |
| `content.json` | `{metadata, blocks[], links[], visual_assets[]}` | a **DoclingDocument**: `texts[]`, `tables[]`, `pictures[]`, `groups[]`, `body`/`furniture` trees. Load it with `docling_core.types.doc.document.DoclingDocument.model_validate` — its schema is owned and versioned by docling-core (see `tool_versions.docling_core`), which is the interoperability win: LangChain/LlamaIndex/MCP integrations consume it natively. The v2 `metadata` object moved to the manifest's `page_metadata` |
| `content.md` / `content.txt` | hand-written serializers | docling's serializers; markdown keeps figures, and each harvested picture's OCR text prints as its description. **The v2 YAML frontmatter (title/source/captured_at/schema_version) is gone** — that metadata lives in `manifest.json` (`title`, `source`, `captured_at`, `bundle_format`) |
| `content.doctags` | — | new: docling's DocTags serialization |
| `content.html` | ad-hoc-cleaned clone | the nh3-sanitized standalone snapshot, **visible content only**, with harvested asset references rewritten to `assets/` |
| `chunks.jsonl` | `{id, source, heading_path, block_ids, text, sha256}` | `{id, text, meta: {doc_items, headings, kind, page, locator, sha256}}` — see below |
| `assets.json` | — (asset records lived inside `content.json`) | new: typed records for `visual_assets` (with OCR text/confidence/language), `form_fields`, and `embedded_media` |
| `tables/*.csv` | from block rows | from docling `TableItem` grids (same cell text, spans resolved) |
| `links.json`, `structured-data.json`, `accessibility.yaml`, `assets/*.png`, `source/` | unchanged | unchanged |

## Reading v3 chunks

```json
{
  "id": "ch_000042",
  "text": "Results\nLatency\n…chunk text…",
  "meta": {
    "doc_items": ["#/texts/17", "#/tables/2"],
    "headings": ["Results", "Latency"],
    "kind": "text",
    "page": null,
    "locator": "#/texts/17",
    "sha256": "…"
  }
}
```

- `doc_items` are DoclingDocument `self_ref` pointers — JSON-Pointer-style
  anchors into `content.json` (`#/texts/17` is `content.json["texts"][17]`).
  Chunks are traceable to the exact source items.
- `locator` is the leading `doc_items` entry. v2's CSS-path DOM locator is not
  derivable from the docling model; anchor into `content.json` instead
  (docs/09 P2 correction).
- `page` is `null` on the web path (no print-pagination mapping exists from
  the DOM); real page numbers remain a protected-path concept.
- `kind` is `text`, `table`, or `figure`. Figure chunks replace v2's
  `type: "visual"` chunks and reference the `PictureItem`.
- `text` is contextualized (the heading path precedes the content), exactly as
  docling's `contextualize` spells it. `sha256` digests the UTF-8 bytes of the
  `text` field.

## Where v2's extra block types went

docling's HTML backend does not model everything v2's blocks did. Nothing
vanished (docs/02 fidelity mapping); it moved:

| v2 block type | v3 home |
|---|---|
| `form-value` (incl. `[REDACTED PASSWORD]` records) | `assets.json` → `form_fields[]` (`name`, `value`, `locator`) |
| `embedded-media` (incl. `<track>` caption records) | `assets.json` → `embedded_media[]` (`media_type`, `source_url`, `title`, `tracks[]`, `locator`) |
| `definitions` | modeled natively: docling list groups (`groups[]` of `list_item` texts) |
| `figure` | `pictures[]`; the harvested asset's OCR text/confidence/language attach as annotations (`description` + `misc` with the `webshot` record) |

Two consumer-visible nuances:

- Page chrome (`header`/`nav`/`footer`/`aside` text) sits on the DoclingDocument
  **furniture** content layer: present in `content.json`, omitted from
  `content.md`/`content.txt`/chunks — matching what v2's block extraction
  recorded.
- `<iframe>` elements no longer appear in `content.html` (sanitizer allowlist);
  their records are in `assets.json.embedded_media` and their visible state is
  captured as a screenshot asset, as before.

## The escape hatch: `--legacy-bundle`

If your consumer cannot move yet, capture with `--legacy-bundle`: the bundle
additionally contains `legacy/content.json` and `legacy/chunks.jsonl` in the
exact v2.2 format (emitted by v2.2's own code, validated against the frozen
schemas), listed in the manifest with `legacy: true`.

**It ships in v3.0 and is removed in v3.1.** Treat it as time to migrate, not
a place to live.

## Exit codes

v2.2 exited 1 for every failure. **v3 never emits 1 at all** — every failure
returns one of 2–9 from the taxonomy in [docs/04-spec.md §3](04-spec.md), and
that is the point: a navigation timeout (3), an authentication wall (4) and a
bad flag (2) are different problems and a script should be able to tell them
apart. A `$? -eq 1` check must become `-ne 0`; a script that wants to retry on a
transient failure and give up on a permanent one can now do so:

```bash
webshot "$url" --output out.pdf
case $? in
  0) ;;                       # captured
  3) retry_later ;;           # the page never arrived
  4) refresh_credentials ;;   # the site wants a sign-in
  *) give_up ;;               # a bad request, or a broken environment
esac
```

Ctrl-C exits **130**, the shell's own SIGINT convention, and is deliberately
outside the taxonomy.

## The QA report is at schema v2

`--report` writes a different shape in v3, and two of the changes will break a
consumer that reads the file rather than glancing at it:

| v2.2 | v3 | Why |
|---|---|---|
| `ai_summary` | **`counts`** | `ai_summary` carried different keys on the web path (`items`, `tables`, `links`, …) and the protected path (`pages`, `chunks`, `text_characters`), so it could never be typed. `counts` has one shape, and a number a capture path does not produce is `null` rather than `0` |
| `validation` absent unless `--validate-pdf` ran | **`validation` always present** | Absence made "was not checked" and "was checked and found nothing" identical. The block now carries `pdf_readable`, `pdf_pages`, `page_count_invariant`, and `pdfa` — the last being `null` when the gate did not run |

Everything else is additive: `schema_version: "2"`, `timings` (seconds per
pipeline stage), `options` (as resolved, after flags/env/file — with the
credential paths reduced to `auth_mode`), `manifest_sha256`, and `exit_code`.
Validate against `webshot schema qa-report`.

**A failing run now writes a report too**, which is what makes `exit_code`
worth reading: in v2.2 — and in v3 before its 5.1 audit — a run that failed
before publication wrote nothing, so every report that existed said 0. A
failure report publishes what the run genuinely knew and `null` for what it
never reached (`title`, `pages`, `counts`, `validation`, and `options` when the
options themselves were the problem), so a consumer that reads those fields
should expect null on a failure. It adds one key, `error`, which is **absent
entirely from a successful report** — `"error" in report` is the test.

`manifest.json` also gains one additive field on both paths,
`content.ocr_words`. A consumer that ignores unknown keys needs no change.

One encoding change comes with the version bump: the report is now written the
way every other WebShot artifact is, as **UTF-8 without `\uXXXX` escapes**. A
title containing `Café` was `"Caf\u00e9"` in a v2.2 report and is raw UTF-8 in a
v3 one. Read the file as UTF-8 — `json.load(open(path, encoding="utf-8"))` — and
nothing changes; a reader that assumed ASCII-safe bytes, or that relied on the
platform default encoding on Windows, needs the explicit encoding.

## New in v3, and optional

Nothing below is required to migrate; they are the reasons to.

- **`--config` and `WEBSHOT_*`** — settings in a TOML file and the environment,
  resolved as flags > env > file > defaults. The file is read only from
  `--config` or `WEBSHOT_CONFIG`; WebShot never picks up a `webshot.toml` from
  the working directory. See [Configuration](guide/configuration.md).
- **`webshot mcp`** — the bundle, served to an agent over MCP instead of walked
  on disk. If you are integrating one, read
  [the MCP guide](guide/mcp.md) first: returned page content is untrusted data,
  and the client is what has to treat it that way.
- **`webshot schema`** — print or regenerate any published contract, so a
  consumer can validate against the schema that shipped with its WebShot rather
  than one copied from a repository.
