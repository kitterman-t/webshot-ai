# 02 — Target architecture

## Design principle

v3 is a **pipeline of five stages** with custom code concentrated in the two
stages the ecosystem does not cover (capture, protected-viewer capture) and
thin, replaceable **bridge modules** wherever an upstream component is adopted.
Each bridge is the only file allowed to import its upstream library, so any
component can be swapped by rewriting one file against the same internal
contract.

```
             ┌─────────────────────────────────────────────────────────────┐
             │                        ACQUIRE                              │
             │  URL ────────────────────────────────┐                      │
             │  local file ─ MarkItDown/Markdown ─▶ HTML (adapters bridge) │
             └───────────────────────────┬─────────────────────────────────┘
                                         ▼
             ┌─────────────────────────────────────────────────────────────┐
             │                    CAPTURE  (custom core)                   │
             │  Playwright session: auth (storage_state | persistent       │
             │  profile), scroll/lazy-load, readiness waits, clean/        │
             │  faithful prep, selector isolation, canvas rasterization,   │
             │  visual-asset harvest, OCR pass, sanitized DOM snapshot     │
             │  (nh3), aria snapshot, JSON-LD, links                       │
             └───────┬──────────────────────────────────────┬──────────────┘
                     ▼                                      ▼
   ┌───────────────────────────────┐      ┌────────────────────────────────┐
   │        RENDER (PDF)           │      │       EXTRACT (bundle)         │
   │ page.pdf(tagged, outline)     │      │ sanitized HTML ─▶ docling-slim │
   │ header/footer templates       │      │ ─▶ DoclingDocument (+ OCR      │
   │ pypdf structural validation   │      │ annotations) ─▶ md/txt/html/   │
   │ [optional] OCRmyPDF → PDF/A   │      │ json/doctags, tables → CSV,    │
   │ pypdf AF-embed bundle files   │      │ HybridChunker → chunks.jsonl   │
   └───────────────┬───────────────┘      └───────────────┬────────────────┘
                   └─────────────────┬────────────────────┘
                                     ▼
             ┌─────────────────────────────────────────────────────────────┐
             │                   PACKAGE & PUBLISH (custom, thin)          │
             │  manifest (pydantic → JSON Schema), per-file SHA-256,       │
             │  atomic directory swap, QA report, exit codes               │
             └─────────────────────────────────────────────────────────────┘

  PROTECTED-VIEWER PATH (parallel to the above after CAPTURE):
    viewer enumeration + resumable page PNGs + count verification (custom)
      ─▶ img2pdf (lossless pack) ─▶ OCRmyPDF (invisible text layer,
      sidecar text, optional PDF/A) ─▶ transcript appendix rendered as
      HTML via RENDER stage ─▶ pypdf merge + bookmarks + AF embeds
```

## Module map

Proposed `src/` layout. LOC figures are budgets, not measurements.

| Module | Kind | Budget | Responsibility |
|---|---|---:|---|
| `webshot/cli.py` | custom | 250 | argparse surface, back-compat flag mapping, settings resolution onto the namespace, and the `doctor` / `schema` / `mcp` subcommands. Translation only — the rules live on the options model (docs/09 P4-8). Imports each stage **inside the function that uses it**, so `--version` and `--help` do not load the capture stack; the deferred imports sit inside the `try` blocks that classify failures, so a dependency missing at first use still exits with a §3 code (docs/09 P10-6) |
| `webshot/pipeline.py` | custom | 300 | **stage sequencing**: ACQUIRE → CAPTURE → RENDER/EXTRACT → PACKAGE, the protected-viewer branch, atomic publication, cleanup (added in Phase 0 — see docs/09 P0-1) |
| `webshot/doctor.py` | custom | 200 | environment diagnostics per [04-spec.md](04-spec.md) §1.2 (added in Phase 0) |
| `webshot/version.py` | custom | 5 | the released version string, previously duplicated across three modules |
| `webshot/__init__.py` | custom | 60 | the public API spec §7 promises, resolved on first access (PEP 562). Eagerly re-exporting `convert_url_to_pdf` here put the whole capture stack behind `import webshot.version`, and therefore behind every command (docs/09 P10-6) |
| `webshot/onnxruntime_telemetry.py` | **bridge → onnxruntime** (the ONLY module that touches it; WebShot never imports it to do work) | 150 | onnxruntime's telemetry, switched off before it can report a capture: the `ORT_DISABLE_TELEMETRY` default that stops the POSIX uploader (docs/09 P10-14), and on Windows a post-import hook that calls `disable_telemetry_events()` before whoever imported onnxruntime can build a session (P10-26). Run from the package init, which every path into WebShot executes first |
| `webshot.py` (repo root) | custom | 70 | back-compat shim for `python webshot.py …`: loads `src/webshot/` as the `webshot` package and substitutes it into `sys.modules`. Forwards names through its own `__getattr__` for the same reason as the row above |
| `webshot/config.py` | custom | 195 | the options model every stage is configured by, and the capture result. Holds **every rule about whether a set of options makes sense**, so the CLI and the MCP server cannot be given combinations the other would refuse (docs/09 P4-8). *The TOML/env half moved to `settings.py` in Phase 4 (P4-3): loading and layering settings is a different job from being the options.* |
| `webshot/settings.py` | custom (new) | 380 | `webshot.toml` via stdlib `tomllib`, `WEBSHOT_*`, and the precedence spec §1.1 fixes — flags > env > file > defaults. Explicit-path-only loading (§6.10); the `[mcp]` table the server reads (added in Phase 4) |
| `webshot/report.py` | custom (new) | 195 | builds the QA report (schema v2) from a capture result and its resolved options; the contract models stay in `bundle/manifest.py` (added in Phase 4) |
| `webshot/errors.py` | custom | 60 | error taxonomy → exit codes (see [04-spec.md](04-spec.md)). `classify` answers `ImportError` with 9, which the deferred imports made reachable at runtime (docs/09 P10-6) |
| `webshot/acquire/router.py` | custom | 60 | URL vs path; MIME sniff; size guard (100 MB) |
| `webshot/acquire/adapters.py` | custom | 360 | the format table (WebShot's own routing, not a byte sniff), the guards — 100 MB, `defusedxml` on every XML input, archive expansion/member/zip-slip limits — the wrapper-page template, Markdown→HTML rendering, and the local-document nh3 allowlist. *Split from the bridge in Phase 3 (docs/09 P3-6): this table called one module the MarkItDown bridge and budgeted it at 80 LOC, but most of what it holds is presentation and policy that no upstream provides* |
| `webshot/acquire/markitdown_bridge.py` | **bridge → MarkItDown** (the ONLY markitdown importer) | 90 | bytes + a format hint in, Markdown out; corrects two converter behaviors that lose table content (TSV has no converter; cell delimiters are unescaped — docs/09 P3-1); rewrites the missing-dependency advice to name `webshot[office]` |
| `webshot/capture/session.py` | custom (core) | 180 | browser lifecycle; storage_state / persistent-profile auth; interactive first run; owner-only perms |
| `webshot/winacl.py` | **bridge → Windows security API** (the ONLY caller of advapi32, through ctypes) | 580 | spec §6.3 on Windows: creates a profile with a protected owner-only DACL, rewrites one that fails, and checks the owner and DACL of **every entry**, because Windows skips traverse checks by default and a private directory does not make its files private. Callers get `Security`, `Ace` and `Exposure`, never a ctypes value. The rule is a pure function, so it is tested on every platform; the ctypes calls run only on Windows and are type-checked everywhere by `mypy --platform win32` (docs/09 P10-25) |
| `webshot/capture/prepare.py` | custom (core) | 260 | clean/faithful, excludes, selector isolation, waits, scroll loop with safety limit |
| `webshot/capture/discover.py` | **bridge → Trafilatura** (the ONLY trafilatura importer) | 90 | the article's *text* and the page's declared metadata; `extensive=False` so no date is inferred (docs/09 P3-3). The DOM-side half — locating that text and choosing between it and the v2 static list — is `capture/prepare.py`, which records the winning strategy for the manifest |
| `webshot/capture/visuals.py` | custom (core) | 220 | asset harvest (img/svg/canvas/video/iframe/css-bg), canvas rasterization, dedupe by SHA-256 |
| `webshot/sanitize.py` | **bridge → nh3** (the ONLY nh3 importer) | 70 | one `nh3.clean` call and the two allowlists it is given as data — the DOM snapshot's and a rendered local document's — with spec §6.5's URL-scheme rule defined once for both. *Added in Phase 3 (docs/09 P3-8): the second allowlist is a second policy, not a second importer* |
| `webshot/capture/snapshot.py` | custom | 220 (→ ≈ 160 at v3.1) | sanitized standalone HTML (the snapshot's allowlist, applied through `sanitize.py`), visibility filtering, aria snapshot (password-scrubbed — docs/09 P2-1), JSON-LD, links, and the capture-side fidelity/locator records. *Budget raised from 100 in Phase 2 (P2-8): the visibility filter and sanitizer policy moved here by design, and the block-record JS stays as `--legacy-bundle`'s feeder until v3.1* |
| `webshot/ocr/engine.py` | custom (protocol) | 80 | the `OcrEngine` protocol — `recognize(image, language, psm) -> OCRResult`, plus `unavailable_reason()` so a missing engine degrades rather than raises — and `resolve_engine()`, which imports an implementation only when `--ocr-engine` names it. *Seam is at asset recognition, not page recognition: the protected path's text layer is OCRmyPDF's and its word boxes are Tesseract TSV (docs/09 P3-7)* |
| `webshot/ocr/tesseract.py` | custom (retained v2 subprocess code) | 220 | default engine; tesseract binary via subprocess, TSV word output — v2's proven path, no pytesseract dependency. Covers both callers: harvested visual assets (text + confidence) and captured document pages (**word coordinates**, moved here from the protected path in Phase 1 — docs/09 P1-7) |
| `webshot/ocr/rapid.py` | **bridge → RapidOCR** *(extra)* | 90 | pip-only fallback engine (ONNX, no system binary, models in the wheel); quiets the library's per-model logging with a filter, because a level does not hold (docs/09 P3-4) |
| `webshot/render/pdf.py` | custom | 130 | `page.pdf(tagged=True, outline=True)` + header/footer templates; also prints a standalone HTML document (the transcript appendix) through the same call, and reports whether a produced file really is tagged |
| `webshot/render/clipping.py` | custom | 290 | after the render, the text the printed page cuts off at its left or right edge, and the text a scrolling box hides because a PDF cannot scroll, as one manifest warning each (docs/09 P16-8). The width is read back from Chromium with a one-page probe print rather than derived from the paper, because `@page` rules and shrink-to-fit both move it; warns rather than reflowing the layout (docs/09 P16-1, P16-4) |
| `webshot/render/frames.py` | custom | 230 | before the bundle, each frame the page can script grown on trial to its document's height at the printed width (the clipping check's probe), innermost first; what still falls outside a frame's box, as a manifest warning naming the frame (docs/09 P16-16) |
| `webshot/render/embed.py` | **bridge → pypdf** | 250 | associated-file embedding with `AFRelationship`, `/UF`, MIME `/Subtype` and the catalog `/AF` array, shared by both paths; payload collection and the reader's README. *Named `render/post.py` in the original plan and never written — the web path went without embeds until it was built (docs/09 P6-1); structural validation lives in `render/pdf.py`* |
| `webshot/extract/docling_bridge.py` | **bridge → docling-slim** (the ONLY docling importer — docs/09 P2-8) | 250 | sanitized HTML → DoclingDocument **via `HTMLDocumentBackend` directly — the top-level `DocumentConverter` crashes on slim installs (docs/09 S3)**; corrects four backend behaviors at the seam (title-tag item, dropped table captions, furniture misfiling, doctags code-language crash — P2-3); positional picture↔asset matching with a hard count check (P2-4); OCR + captions as picture annotations; HierarchicalChunker seeds with `self_ref` provenance. *Budget raised from 150 in Phase 2: the four corrections and the chunker moved in (P2-8)* |
| `webshot/extract/exports.py` | custom (thin writer over bridge outputs) | 40 | writes content.{json,md,txt,doctags} + tables/*.csv; every string/grid arrives pre-serialized by docling through the bridge |
| `webshot/extract/chunks.py` | **bridge → chonkie** | 60 | size-splits bridge seeds at the character budget; spec §2 chunk records (doc_items, headings, kind, page, locator, sha256) |
| `webshot/extract/guidde.py` | **bridge → Guidde** (the ONLY module knowing its API shape) | 480 | playbook detection, interception and parsing: a playbook id is a path segment on every Guidde surface, so detection is an attribute scan over the prepared DOM (docs/09 P7-2); the hydration call is authenticated, so the playbook is read off the response the app makes for itself rather than requested (P7-1); per-step subtitles are offset onto the playbook's clock, which reproduces Guidde's own published `.vtt` (P7-3). What crosses out is `Walkthrough`/`WalkthroughStep`/`TranscriptCue` — never a response dict. Those names are provider-neutral ahead of a second producer rather than after one (docs/13); `Walkthrough.provider` says which service made it and `Walkthrough.id` is that service's own identifier |
| `webshot/bundle/captions.py` | custom (new) | 270 | preference-order path 1: reads a media element's `<track>` **through the page** by setting `mode='hidden'`, so the request crosses `context.route` like any subresource and needs no host allowlist (P7-1's shape, P7-6's reason). Only `kind="captions"` becomes a transcript — an allowlist, because HTML defaults a missing `kind` to `subtitles`; a translation is kept as its own artifact and never inlined or chunked |
| `webshot/bundle/videos.py` | custom | 700 | the walkthrough artifacts (`walkthrough.md`, `steps.json`, `transcript.txt`, `.vtt`), per-step chunks with full provenance, the `content.md` inlining, the asset downloads and their dedupe, and the record for a plain `<video>` the bundle could **not** document. Knows nothing of Guidde — it reads the bridge's dataclasses |
| `webshot/journey/model.py` | custom (new) | 150 | the enumerated tree, its **synthetic path ids**, and the two assertions that decide whether an outline may be used as a capture contract — per-node counts and the diff of two walks. Pure, so both are tested without a browser |
| `webshot/journey/walk.py` | custom (new) | 480 | stage 1 of the journey walker (docs/13): open every expander, read the tree, **open no module**. Carries `DomContract` — Continu's measured markup (P11-3), keyed on `aria-expanded`/`data-index`/`aria-controls`/`aria-label` and deliberately never on an Emotion hash or a React `useId`. Clears the completion modal that blocks every visit (P11-1), polls for a track view that is not ready when the click returns (P11-2), and witnesses every expansion with `aria-expanded` because a collapsed panel keeps its rows (P11-4) |
| `webshot/journey/capture.py` | custom (new) | 420 | stage 2: capture exactly the module set the enumeration listed, on the page the walk already has. Refuses every `assessment` (P12-2), asserts counts at every level, paces per host (docs/11 §2) and reports whether the floor ever bound, and keys resume on the synthetic path **plus a digest of the outline it was built against** — a path alone resolves against any tree of the same shape |
| `webshot/journey/media.py` | custom (new) | 170 | the corpus media store: one copy of every distinct asset **and every module that referenced it**, consuming the `sha256` each record already carries. Scope is files with a hash — `visual_assets` and `video_assets`; `embedded_media` is a remote reference with nothing to hash. Built beside the module bundles rather than out of them, so a module stays hand-off-able |
| `webshot/journey/index.py` | custom (new) | 260 | the corpus index — `index.json` and `index.md` at the root, built from what capture already recorded and never by re-walking. Carries the five-level outline, both durations (P9-8), video kind and provenance, and **every refusal with its reason**: an index listing only what was captured describes a journey with no assessments in it, which is absence implied |
| `webshot/journey/cli.py` | custom (new) | 180 | the `webshot journey URL --dry-run` subcommand; reuses `capture/session.launch_context` rather than launching a second browser, so an auth profile gets the same owner-only treatment and the same network policy |
| `webshot/extract/legacy.py` | custom (transitional) | 180 → 0 at v3.1 | v2.2's emitters verbatim, alive only for `--legacy-bundle` (docs/05 task 2.5); deleted with the flag |
| `webshot/bundle/manifest.py` | custom | 120 (450 until v3.1) | pydantic models; JSON Schema export to `schemas/`. Carries the **frozen v2.2 contract models** as well until `--legacy-bundle` is removed at v3.1, which is why it is over budget through v3.0 |
| `webshot/bundle/build.py` | custom (transitional) | 450 → ≈ 0 | v2.2 bundle assembly + manifest writing, **web and protected-viewer both** (the viewer bundle moved here in Phase 1, out of `protected/assemble.py`, which this table always described as a bridge — docs/09 P1-7); emptied as Phases 1–3 move each artifact behind a bridge (added in Phase 0 — see docs/09 P0-1) |
| `webshot/bundle/localpaths.py` | custom (new) | 180 | `--local-paths relative`: records a local source and each `file:` URL under its directory relative to that directory as the bundle is written, so no digest is re-recorded; then reads every bundle file for the directory in either spelling and warns, by file name, about any that still holds it (docs/09 P14-61) |
| `webshot/bundle/publish.py` | custom | 100 | checksums, atomic tmp-dir → rename swap, QA report |
| `webshot/protected/viewer.py` | custom (core) | 350 | SharePoint viewer enumeration, resumable page capture, count verification |
| `webshot/render/appendix.py` | custom | 100 | the appendix mechanism both paths share: the back-link marker scheme, the pypdf rewrite that turns each marker into an internal jump, and the refusal to publish a file where one survived. *Extracted in Phase 7 when the web path grew an appendix of its own — the protected-viewer golden is byte-identical across the move* |
| `webshot/render/video_appendix.py` | custom | 300 | authors the embedded-video appendix as HTML and merges it after the captured pages, with a bookmark per video and per step. Each step heading is itself a back-link, so the merge reads every step's page off its own annotation instead of estimating it. The appended pages are **not** tagged (docs/09 S8) and the manifest says so |
| `webshot/protected/appendix.py` | custom | 80 | authors the transcript appendix as HTML for the RENDER stage; back-links are marker URIs the composition rewrites (added in Phase 1) |
| `webshot/protected/assemble.py` | **bridge → img2pdf + OCRmyPDF** | 250 | pack → OCR layer → appendix merge → embeds, plus the invariant checks and atomic publication that sequence them. *Budget corrected from 120 in Phase 1 (docs/09 P1-7): the original figure covered only the four composition steps* |
| `webshot/validate/verapdf.py` | **bridge → veraPDF** (external tool) | 180 | PDF/A conformance of the published file via the local CLI or `verapdf/cli`; degrades to a warning when neither is available (added in Phase 1) |
| `webshot/mcp_server/policy.py` | custom (new) | 350 | every MCP refusal as a plain function over plain values — roots confinement resolved through symlinks, the private/loopback/link-local rule and its `final_url` re-check, profile-name and profile-readiness rules ([04-spec.md](04-spec.md) §6.8a–f) — plus the resolved `Boundary`, so `webshot mcp --print-roots` can audit a server on a machine without the extra. Imports nothing upstream, which is what lets each guardrail be tested without a protocol round-trip |
| `webshot/mcp_server/service.py` | custom (new) | 580 | what the tools *do*: build options from config + call (an **allowlist** of settable fields — docs/09 P4-8), stage a capture inside the output root and publish only after every check passes, and read a bundle back through its published contract models. Protocol-free, so `tests/test_mcp_service.py` exercises it without `webshot[mcp]` installed (P4-9) |
| `webshot/mcp_server/server.py` | **bridge → `mcp` SDK** (the ONLY mcp importer) | 280 | tool registration on `MCPServer`, the `Context`→callback progress adapter, and the strict-arguments middleware that rejects an undeclared parameter instead of dropping it (docs/09 P4-1). *Budget cut from 700 in the Phase 4 cleanup review: 400 lines of it never touched the SDK (P4-9)* |
| `webshot/mcp_server/results.py` | custom (new) | 160 | the published result models, each content-bearing one carrying the untrusted-data notice |
| `webshot/mcp_server/cli.py` | custom (new) | 115 | the `webshot mcp` subcommand; imports the SDK lazily so `[mcp]`-less installs still run |
| **Total (CANONICAL — 05's rollup derives from this table)** | | **≈ 6,540: ≈ 5,054 custom (of which ≈ 1,350 is core capture/protected pipeline logic; the rest is extracted infra + the net-new MCP server) + ≈ 1,486 bridge/glue** | |

*Total revised in Phase 0 (docs/09 P0-1): +505 for `pipeline.py`, `doctor.py`,
and `version.py`, which were unattributed rather than absent. `bundle/build.py`
is transitional and counted at ≈ 0 for the v3 target; `bundle/manifest.py` is
counted at its post-v3.1 budget.*

*Total revised again in Phase 1 (docs/09 P1-7): +560, for the page-OCR pass
that moved into `ocr/tesseract.py` (+160), the appendix author (+80), the
veraPDF bridge (+180), the corrected `protected/assemble.py` budget (+130), and
the appendix render in `render/pdf.py` (+10). The G1 claim is unaffected: none
of these implement OCR layering, extraction, chunking, format parsing, or
sanitization — the veraPDF and img2pdf/OCRmyPDF rows are bridges, and the OCR
pass is v2's retained subprocess code with a new home.*

*Total revised again in Phase 3 (docs/09 P3-6, P3-8): +540, and unlike the two
revisions above this one is partly new work rather than accounting.
`acquire/adapters.py` rises 80 → 360 and splits off a 90-line MarkItDown
bridge; `capture/discover.py` 60 → 90; `ocr/engine.py` 40 → 80;
`ocr/rapid.py` 60 → 90; and `sanitize.py` is a new 70-line bridge row (P3-8),
which is a move rather than an addition — the code it holds came out of
`capture/snapshot.py`. Where it went: the bespoke CSV/JSON/JSONL renderers
were deleted (≈ 75 lines), and ≈ 195 arrived as the format-routing table, the
archive-safety and XML guards spec §6 requires, five new input formats, and
the docstrings that say why each piece is not delegated. The G1 claim holds —
no custom code implements format parsing; what remains is routing, guards, and
presentation.*

*Total revised again in Phase 4 (docs/09 P4-2, P4-3, P4-5, P4-9): +1,915, almost
all of it real work on the two interfaces this phase exists for, and the largest
single revision in the project. The MCP server goes from one 250-line row to
five rows totalling ≈ 1,515, of which only ≈ 296 is the SDK bridge: the tool
wiring is roughly the budgeted size, and the six guardrails, the staged
publication that makes the redirect rule a refusal to publish, the bundle
readers, the published result models, and the subcommand are not.
`settings.py` (+380) and `report.py` (+195) are the config layer and the QA
report, both specified in 04 and previously unowned by any row; `config.py`
grows by 75 as the option rules move onto the model that both interfaces build
(P4-8). The G1 claim is unaffected in the direction that matters: none of this
reimplements anything upstream provides — nothing in the ecosystem exposes*
capture *over MCP, which is exactly ADR-0009's argument for building it. What it
does show is that a security boundary costs more than the surface it guards, and
the budget was written as if it would not.*

Rows marked *(added in Phase 0)* are not new work: they account for code that
already existed in the v2.2 monolith and had no row of its own. Phase 0's
decomposition put every function in the module this table names.

The "custom (core)" rows — session, prepare, visuals, protected viewer — are the
audit-confirmed unique value. Everything marked **bridge** wraps an upstream
project and must not leak upstream types outside its module (internal dataclasses
only at the seams).

## Data contracts

### The internal handoff: `CaptureResult`

Produced by CAPTURE, consumed by RENDER and EXTRACT. Frozen dataclass:

- `final_url`, `title` (the live Playwright page is passed alongside to the RENDER stage, stage-scoped — deliberately NOT part of the frozen contract)
- `sanitized_html: str` — standalone, script-free, asset refs rewritten to `assets/`
- `assets: list[VisualAsset]` — kind, bytes SHA-256, dimensions, alt/aria/caption/nearby-heading context, source URL, OCR words (text, confidence, bbox, lang)
- `aria_snapshot: str`, `json_ld: list[dict]`, `links: list[Link]`
- `metadata: PageMetadata` (Trafilatura-enriched: author, date, sitename)
- `warnings: list[Warning]`, timings

### The bundle (`report.ai/`), format version 3

| File | Producer | Notes |
|---|---|---|
| `manifest.json` | bundle/manifest | `bundle_format: 3`, provenance, auth mode, counts, warnings, checksums, PDF details, tool versions incl. upstream lib versions. Phase 3 added two additive records: `discovered_metadata` (what Trafilatura read — kept separate from `page_metadata`, which is what the DOM declared) and `content_discovery` (which strategy chose the content region, and what it chose) |
| `content.json` | docling serializer | **lossless DoclingDocument JSON** (breaking change vs v2 — see migration in [05-implementation-plan.md](05-implementation-plan.md)) |
| `content.md` / `content.txt` / `content.doctags` | docling serializers | Markdown keeps figures + OCR text (picture annotations print natively) |
| `content.html` | capture/snapshot | the sanitized standalone snapshot (single producer); visible content only, asset refs rewritten to `assets/` |
| `chunks.jsonl` | extract/chunks | one JSON object per line; schema below |
| `accessibility.yaml` | capture/snapshot | Playwright aria snapshot (unchanged from v2) |
| `structured-data.json`, `links.json` | capture/snapshot | unchanged |
| `tables/*.csv` | docling table export | one lossless CSV per table |
| `assets/*.png` + `assets.json` | capture/visuals (records; the bundle writer emits the file) | binary + typed records (OCR text, conf, lang, sha256, context) **plus the fidelity records docling does not model: `form_fields` and `embedded_media`** |
| `legacy/content.json`, `legacy/chunks.jsonl` | extract/legacy (`--legacy-bundle` only) | exact v2.2 format, manifest-flagged `legacy: true`; v3.0 only |
| `ocr/page-NNN.tsv` | protected path | word coordinates + confidence (pytesseract TSV) |
| `source/` | acquire | preserved local input copy (never for URLs) |

### Chunk record schema (informal; JSON Schema published in `schemas/`)

```json
{
  "id": "ch_000042",
  "text": "…contextualized chunk text…",
  "meta": {
    "doc_items": ["#/texts/17", "#/tables/2"],
    "headings": ["Results", "Latency"],
    "kind": "text | table | figure",
    "page": null,
    "locator": "the leading doc_items self_ref — a stable content.json anchor",
    "sha256": "sha256 of the UTF-8 bytes of the text field"
  }
}
```

`page` is **protected-path only** (real page numbers exist there); the web path
has no print-pagination mapping from the DOM, so `page` is null and `locator`
is the positional reference. *Amended in Phase 2 (docs/09 P2-5): the plan
called for a DOM anchor, but a CSS path is not derivable from the docling
model — the locator is the chunk's leading DocItem `self_ref`, a
JSON-Pointer-style anchor into content.json.*

**Fidelity mapping (v2 → v3, MUST).** Every v2 block type maps to a
DoclingDocument construct or is explicitly deprecated — nothing vanishes
silently: headings/paragraphs/lists/tables/code/figures → native items;
definition lists → docling list/group items (**confirmed native in Phase 2** —
no custom annotation needed); **form fields (including `[REDACTED PASSWORD]`
value records) and embedded-media records (including `<track>` captions)** are
NOT modeled by the HTML backend and are therefore carried in `assets.json`
(`form_fields`, `embedded_media` — each with the capture-side CSS locator);
figure→asset links preserved via PictureItem annotations (a `misc` annotation
carries the asset id, sha256, and OCR confidence/language; a `description`
annotation carries the OCR text, which docling's own serializers print);
v2's computed-style visibility filtering runs in CAPTURE (before docling) so
extraction sees visible content only. Page chrome (header/nav/footer/aside)
sits on the DoclingDocument furniture layer: in content.json, out of the
exports — matching what v2's block extraction recorded (docs/09 P2-3). The
parity harness measures all of these (06).

`doc_items` are DoclingDocument `self_ref` pointers — chunks are traceable to
the exact source items, which is the interoperability win over v2's bespoke
block IDs.

### The PDF deliverable

- Web path: Chromium **tagged** PDF with **outline**, header/footer, then
  associated-file embeds (before validation, so the manifest hashes the file
  that is actually published), then pypdf validation, then embeds (`manifest.json`,
  `content.json`, `chunks.jsonl`, `content.txt`) with
  `AFRelationship=/Data` and MIME types set. Metadata records bundle format
  and source-page counts.
- Protected path, in this order: (1) source pages preserved at captured
  quality (img2pdf, lossless); (2) OCRmyPDF adds the invisible positioned OCR
  layer with **`output_type='pdfa-3'`** — PDF/A-**3** explicitly permits
  embedded files, which the A-2b default forbids; (3) the transcript appendix
  (rendered from HTML through the RENDER stage) is appended via pypdf;
  (4) associated files embedded. Steps 3–4 happen after conversion, so
  **PDF/A conformance of the FINAL composed file is a veraPDF acceptance check
  in Phase 1, never an assumption**; if composition breaks conformance, the
  `--pdfa` variant ships without appendix/embeds and the default output
  carries them, with the tradeoff documented. **Result (Phase 1, docs/09 P1-4):
  the composed file with appendix and embeds IS PDF/A-3B conformant — 146 rules
  passed, 0 failures — so the fallback was not taken.** Two things it depends
  on: the embedded files carry `/UF` and an unescaped MIME `/Subtype` (clause
  6.8, docs/09 P1-3), and the composition clones its writer from the OCRmyPDF
  output so the catalog's PDF/A identification and output intent survive —
  building a fresh writer and appending both documents drops them silently. **Appendix tag honesty (spike
  S8, docs/09): pypdf's append does NOT carry the appendix's structure tree
  into the merged file — the appendix is authored as tagged HTML for
  rendering quality, but the merged PDF is not a tagged PDF.** Bookmarks
  separating original from transcript, transcript→source-page links, and the
  searchable text layer are unaffected. Page-count invariant:
  `pdf_pages == source_pages + appendix_pages`, checked before publication.

## Concurrency model

Unchanged philosophy from v2: one page at a time, deterministic. OCR of
harvested assets fans out on a bounded worker pool (CPU count, capped);
OCRmyPDF handles its own multi-core distribution on the protected path.
No network-idle waits anywhere.

## Configuration precedence

CLI flags → `WEBSHOT_*` env vars → `--config webshot.toml` → defaults.
The pydantic options model is the single source of truth; the CLI and MCP
server both construct it.

## Failure philosophy

Fail loudly with a specific exit code and a machine-readable QA report; never
publish a partial bundle (atomic swap); never silently degrade OCR to "no OCR"
without a manifest warning; `webshot doctor` exists so environment problems are
diagnosed before capture, not during.
