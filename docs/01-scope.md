# 01 — Project scope

## Vision

WebShot turns a live web page or a local document into **two synchronized
deliverables from a single capture pass**:

1. A polished, tagged, searchable **PDF** for people, archives, and
   document-aware AI systems.
2. A checksummed, schema-versioned **AI bundle** (Markdown, lossless JSON,
   retrieval chunks, visual assets, OCR, provenance) for retrieval, indexing,
   vision, and agent workflows.

v3 keeps that product definition unchanged. What changes is *how it is built*:
v3 is a composition of maintained open-source components, with custom code
confined to the capabilities the August 2026 landscape audit confirmed exist
nowhere else.

## Why v3 (problem statement)

The v2.2 codebase works and is tested, but ~2,200 of its 3,343 lines
re-implement solved problems:

- `protected_viewer.py` hand-assembles image-backed searchable PDFs — OCRmyPDF's
  entire reason to exist, with 4,400+ commits of edge cases behind it.
- `ai_artifacts.py` hand-rolls semantic extraction, Markdown serialization,
  table CSV export, and chunking — Docling's entire reason to exist, backed by
  IBM Research and the Linux Foundation.
- `source_adapters.py` hand-parses six file formats — MarkItDown's entire
  reason to exist.
- Bundle formats are bespoke, so no downstream tool understands them without
  custom glue.

A solo maintainer cannot out-maintain those projects. Every line delegated is a
line that gets bug fixes, security patches, and format-spec updates for free.

## Goals

| # | Goal | Measure |
|---|---|---|
| G1 | Delegate all commodity functionality to maintained OSS | Measured against the canonical module table in 02: custom (non-bridge) LOC ≤ ~2,300, core pipeline logic ≤ ~1,400; **zero custom code** in: OCR-layer PDF assembly, semantic extraction, chunk splitting, format parsing, HTML sanitization |
| G2 | Preserve every v2.2 user-facing capability | CLI compatibility table in [04-spec.md](04-spec.md) fully green; golden-corpus parity report passes |
| G3 | Make outputs interoperable, not bespoke | `content.json` becomes lossless DoclingDocument JSON; chunks carry DocItem provenance; schemas published |
| G4 | Make the tool directly usable by AI agents | MCP server shipping in-tree (`webshot mcp`) |
| G5 | Prove the accessibility claim instead of asserting it | veraPDF validation wired into CI; report published per release |
| G6 | Keep the dependency tree permissively licensed | CI license gate denies GPL/AGPL/SSPL in installed deps |
| G7 | Stay installable by one `pip install` + `playwright install chromium` | Heavy/optional capabilities behind extras; `webshot doctor` diagnoses missing system binaries |

*G1's size figure was a planning estimate. The measured code size at v3.0.0
and since, and what the growth went into, is recorded under
[05 §LOC budget](05-implementation-plan.md#loc-budget); the "zero custom code"
half of G1 is what the module map in 02 tracks.*

## Non-goals (explicitly out of scope for v3)

- **Bypassing access controls or DRM.** Protected-viewer capture renders only
  what the authorized viewer shows the signed-in user. This is a hard line, not
  a missing feature.
- **Site-wide crawling / spidering.** WebShot is a single-document tool. Batch
  input (a list of URLs) is in scope; link discovery and frontier management
  are not (see [08-roadmap-and-ideas.md](08-roadmap-and-ideas.md) for the
  future interop story with Browsertrix/Crawl4AI).
- **A hosted service or SaaS.** v3 is a local CLI + MCP server. A REST facade
  is a roadmap item, not a v3 deliverable.
- **Perfect OCR or chart-value reconstruction.** OCR remains machine-generated
  and flagged as such; visual assets are always preserved for verification.
- **Windows as a CI-gated platform.** macOS + Linux are gated; Windows gets a
  weekly non-blocking smoke job so "best-effort" produces a signal, but never
  blocks releases.
- **A GUI.**

## Users and primary use cases

| Persona | Use case | What they touch |
|---|---|---|
| Individual researcher / archivist | Turn long articles, docs, dashboards into readable, searchable PDFs with provenance | CLI |
| RAG / search pipeline builder | Ingest pages as section-aware chunks with stable locators and checksums | AI bundle (`chunks.jsonl`, `content.json`), MCP |
| AI agent (Claude, etc.) | "Capture this URL and give me the content" without shell + directory walking | MCP server |
| Compliance / records team | Capture authorized views of SharePoint documents they are permitted to keep a copy of, as self-contained, searchable, verifiable PDFs | `--protected-viewer` path |
| Accessibility-conscious org | Produce tagged PDFs that actually validate | veraPDF gate, tagged output |

## In scope (v3 feature inventory)

Everything in the v2.2 README, restated as v3 commitments:

- HTTP/HTTPS pages; local HTML, Markdown, JSON/JSONL, CSV/TSV, plain text/logs,
  XML, YAML, and common raster/vector images.
- Incremental scrolling with an infinite-scroll safety limit; element-readiness,
  web-font, and image-completion waits; **no network-idle waiting** (deliberate,
  unchanged).
- `clean` and `faithful` modes; CSS-selector isolation preserving inherited
  styles; automatic main-content discovery (now Trafilatura-assisted).
- Visual preservation + OCR for images, SVGs, canvases, video frames, iframes,
  CSS backgrounds; canvas rasterization before print.
- Tagged PDF with document outline, source/title header, `Page X of Y`;
  Letter/Legal/Tabloid/A3/A4/A5, landscape, margins, scale, print/screen media,
  custom CSS.
- Authenticated capture via Playwright storage state or a private persistent
  profile with interactive first-run; owner-only permissions.
- Protected SharePoint PDF-viewer capture: full page enumeration, resumable page
  images, page-count verification before publication, invisible OCR layer,
  visible transcript appendix, embedded machine-readable associated files.
- Atomic publication with structural validation and per-file checksums; JSON QA
  reports; full-page debug screenshots; meaningful nonzero exit codes.

New in v3:

- MCP server (`webshot mcp`) exposing capture and bundle-reading tools.
- `webshot doctor` preflight (system binary + browser + language-pack checks).
- Optional PDF/A output (protected-viewer path first; see ADR-0010 for the
  tag-stripping tradeoff on the web path).
- Optional veraPDF validation report (`--validate-pdf`), local or via Docker.
- Published JSON Schemas for manifest and chunk records; `bundle_format: 3`.
- Config file support (`webshot.toml`) alongside flags.

## Success criteria for calling v3 done

1. Golden-corpus parity: on the fixture corpus, v3 bundles contain the same
   text content, headings, tables, links, and assets as v2 (allowing improved
   structure), verified by the parity harness in
   [06-quality-and-testing.md](06-quality-and-testing.md).
2. All 39 v2.2 CLI options (table regenerated from `build_parser()`, enforced
   by a CI completeness check) behave identically or emit a documented,
   actionable deprecation message; exit-code change (1 → 2-9) documented as
   the one deliberate break.
3. Custom LOC within budget; `pip-licenses` gate green; CI matrix green.
4. Protected-viewer sample document round-trips with page-count guarantee and
   passes veraPDF *well-formedness* checks (full PDF/UA conformance for
   image-based PDFs is a roadmap item, not a v3 gate).
5. An agent can, via MCP, capture a URL and retrieve markdown + chunks without
   touching the filesystem directly.

## Constraints and assumptions

- Python ≥ 3.11 (raised from 3.10 in Phase 1: OCRmyPDF 17.x requires it — docs/09 P1-1).
- Chromium via Playwright remains the only render engine (page.pdf is
  Chromium-only).
- Tesseract remains the default OCR engine (system binary), with a pip-only
  fallback engine available as an extra; language packs are user-installed.
- Ghostscript: see 03 §4 for its actual status (optional for OCRmyPDF ≥ 17;
  used for PDF/A when present; version-checked by `doctor`).
- Single maintainer; every phase in the implementation plan is independently
  shippable and pausable.

## Naming note

`webshot`/`webshot2` are established R packages in this exact problem space and
`shot-scraper` occupies the adjacent Python CLI niche. **Decision required
before any PyPI publication** — tracked as an open question in
[05-implementation-plan.md](05-implementation-plan.md) §Open questions. Renaming
does not block any phase.
