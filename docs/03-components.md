# 03 — Component selection

Every capability slot, the component chosen to fill it, the alternatives that
were seriously considered, and why they lost. Licenses and versions were
verified August 2026; re-verify at adoption time (pin exact versions in
`pyproject.toml`).

Legend: **ADOPT** = becomes a dependency · **OPTIONAL** = extra / external
binary · **REFERENCE** = study, don't link · **REJECT** = considered, not used.

---

## 1. Browser capture engine

| | |
|---|---|
| **ADOPT (retained)** | **Playwright** (Apache-2.0) — already the v2 engine |
| Rejected | Crawl4AI (Apache-2.0): excellent and the closest overall competitor, but adopting it replaces the exact layer that is WebShot's differentiated core (DOM preparation, canvas rasterization, visual harvest) with someone else's abstractions and release cadence. Puppeteer (Node): wrong runtime. Selenium: no CDP-level PDF control. |
| Reference | **Browsertrix Crawler / browsertrix-behaviors** (AGPL-3.0): the best prior art for scroll/autoplay behaviors and profile-based auth. AGPL ⇒ do not bundle `behaviors.js`; keep our own scroll loop and treat their behavior taxonomy as a design reference. |

Key fact: `page.pdf(tagged=…, outline=…)` is Chromium-only and headless-only —
this locks the render engine, which is acceptable.

## 2. Tagged PDF generation

| | |
|---|---|
| **ADOPT (retained)** | **Chromium via `page.pdf(tagged=True, outline=True)`** — Chromium builds the structure tree and heading outline natively; delete any hand-rolled bookmark logic that duplicates it |
| Optional | **Gotenberg** (MIT): for anyone deploying WebShot's job server-side, document it as the sanctioned HTTP alternative; not a CLI dependency |
| Rejected | WeasyPrint (BSD): experimental PDF/UA but no JavaScript — cannot render the dynamic pages WebShot exists for. wkhtmltopdf: unmaintained. reportlab as page renderer: hand-built layout is the thing being deleted |

## 3. PDF post-processing, validation, embedding

| | |
|---|---|
| **ADOPT (retained)** | **pypdf** (BSD) — structural validation; **`add_attachment` with `AFRelationship`** for associated-file embeds, plus merge, bookmarks and link rewriting on the protected path. Three things it does not do for you, all found by veraPDF: the document-level `/AF` array, `/UF` on the file specification, and leaving the MIME `/Subtype` unescaped (docs/09 S2, P1-3) |
| **ADOPT (done, Phase 1)** | **img2pdf** (LGPL-3.0, dynamic use OK per license policy; locked at 0.6.3) — lossless page-image packing on the protected path (direct JPEG/PNG embedding, no re-encode). A custom `layout_fun` keeps WebShot's own page geometry rather than deriving it from a screenshot's meaningless DPI metadata |
| Not used for embeds | pikepdf (MPL-2.0): a good library, and in the tree anyway as an OCRmyPDF dependency. Its attachment API does support AFRelationship (pikepdf issue #463, which an early draft of this table cited as open, is resolved); pypdf is used because it already did the embedding in this codebase ([ADR-0007](adr/0007-pdf-embedding-pypdf.md)) |
| **OPTIONAL gate (done, Phase 1)** | **veraPDF** (GPLv3+/MPL dual — external tool, never linked): the industry PDF/A / PDF/UA validator. Run via a user-installed CLI (1.30.2 via Homebrew) or the `verapdf/cli` Docker image; JSON reports parsed into the QA report. Behind `--validate-pdf` and a non-blocking CI job. Absent validator → warning, never a silent pass |
| Watch | **OpenDataLoader PDF** (Apache-2.0 core): auto-tagging of untagged PDFs per the Well-Tagged PDF spec, veraPDF-validated. The only candidate for making the *image-based protected-viewer* PDFs structurally tagged. Enterprise add-ons gate PDF/UA export; re-evaluate quarterly |

## 4. OCR layer inside PDFs (the sandwich)

| | |
|---|---|
| **ADOPT (done, Phase 1)** | **OCRmyPDF, pinned to the 17.x major** (MPL-2.0; locked at 17.12.1) — `ocrmypdf.ocr()` Python API: positioned invisible text layer, `sidecar=` plain-text, `output_type='pdfa-3'` (A-3 permits embedded files), `skip_text`, multi-core. Replaced the bespoke assembly in `protected_viewer.py`. Pulls **pikepdf, fpdf2, pypdfium2** transitively — tracked in the lockfile/license gate. It is a **core** dependency, not the `pdfa` extra: the text layer is not optional (docs/09 P1-2). It also sets the project's Python floor at **3.11** (docs/09 P1-1) |
| System deps | Tesseract ≥ 5 (Apache-2.0). Ghostscript (AGPL, external binary, never linked): **no longer strictly required by OCRmyPDF ≥ 17** (pypdfium2 rasterization path), used when present for PDF/A conversion — `doctor` reports its presence *and version* (≥ 10.7; 10.06 has known JPEG bugs). One statement of this reality lives here; 01 and 04 defer to it |
| Rejected | pdf2pdfocr: smaller, less maintained. Hand-rolled reportlab text layer (v2 status quo): the thing being deleted |
| Note | Word-coordinate TSVs (`ocr/page-NNN.tsv`) stay on WebShot's own Tesseract subprocess pass — OCRmyPDF doesn't emit TSV directly. Accepted cost: pages are OCR'd twice on the protected path. Optimization (OCRmyPDF plugin hook to reuse its hOCR) is a roadmap item |

## 5. OCR engines (in-page visual pass)

| | |
|---|---|
| **ADOPT (retained, default)** | **Tesseract via subprocess** (Apache-2.0) — v2's proven invocation path (pytesseract was never a dependency; the ledger previously misstated this); word boxes + confidence via TSV; 100+ languages via user-installed packs |
| **OPTIONAL extra (done, Phase 3)** | **RapidOCR** (Apache-2.0, locked at 3.9.2, on ONNX Runtime) — pip-only, no system binary, for environments where Tesseract can't be installed; behind `webshot[ocr-rapid]`, selected with `--ocr-engine rapid`, and reached only through the `OcrEngine` protocol in `ocr/engine.py`. Its three ONNX models ship *inside the wheel*, so recognition needs no download and no network (docs/09 P3-4). Two settings do not carry across: `--ocr-psm` is Tesseract's page model and is warned-and-ignored, and the bundled models are not told a language, so per-asset records leave `language` unset rather than claiming one. The protected path stays on Tesseract — its text layer is OCRmyPDF's and its word boxes are Tesseract TSV |
| Rejected as defaults | EasyOCR (Apache-2.0, pulls torch), PaddleOCR (Apache-2.0, Paddle framework), Surya (restrictive-for-commercial terms): all heavier or license-awkward as *defaults*; the protocol seam keeps them addable |

## 6. Semantic extraction + document model

| | |
|---|---|
| **ADOPT (done, Phase 2)** | **docling-slim** (MIT, LF AI & Data) with extra `[format-html]`, locked at 2.120.3 (docling-core 2.92.0) — **no torch** (spike-verified; CI-asserted since Phase 2 via `tests/test_footprint.py`, which also scans `uv.lock` for every platform). **Spike caveat: the top-level `DocumentConverter` eagerly imports the PDF backend and crashes on slim installs — the bridge MUST use `HTMLDocumentBackend` directly** (docs/09 S3). Sanitized captured DOM → DoclingDocument → lossless JSON / Markdown / DocTags; tables exported per-table; picture items carry our OCR text + captions as annotations. Four backend behaviors the bridge corrects, found at adoption (docs/09 P2): the `<title>` tag becomes a second title item (fed a title-less copy); `<table><caption>` is dropped (re-attached); pre-heading content lands on the skipped furniture layer (reflowed to body unless it came from header/nav/footer/aside); code languages exist that the doctags token set cannot spell (downgraded for that export only) |
| Why it wins | The audit's key asymmetry: Docling has no browser (BeautifulSoup HTML backend, no JS); WebShot has no document model. Composition is strictly better than either alone, and `content.json` becomes an ecosystem format (LangChain/LlamaIndex/MCP integrations exist upstream) |
| Rejected | Unstructured (open-core, heavier, weaker table story for HTML); all2md (MIT but 27-star bus-factor — the problem being escaped); staying bespoke (the problem being escaped) |
| Note | Full `docling` metapackage (~1.3 GB venv with torch) is explicitly NOT the dependency; only docling-slim + named extras. VLM picture description is a roadmap extra |

## 7. Chunking

| | |
|---|---|
| **ADOPT (done, Phase 2)** | **Docling HierarchicalChunker** (in docling-core, no extra, no tokenizer — spike-verified offline, docs/09 S4) for structure-aware chunks with DocItem provenance + **Chonkie** (MIT, locked at 1.7.0, character tokenizer — spike-verified offline) for token-budget splitting of oversized chunks. Adoption correction (docs/09 P2-2): docling-core's chunker package eagerly imports `tree_sitter` and `huggingface_hub` at module import even when only HierarchicalChunker is used — the spike venv satisfied them transitively, so S4's "no extra" held only by accident. Both are small permissive-licensed additions; nothing downloads, nothing infers. The chunker is configured with docling's own markdown table serializer, because the default triplet form drops row-header cells (parity caught it) |
| Optional extra | **Docling HybridChunker** — refuted as default by spike S4: downloads a HuggingFace tokenizer (fails offline) and its extra adds ≈140 MB; available behind an extra for users who accept that |
| Rejected | LangChain text splitters: framework tax for one function. Bespoke chunker (v2): deleted |

## 8. Main-content discovery + page metadata

| | |
|---|---|
| **ADOPT (done, Phase 3)** | **Trafilatura** (Apache-2.0, locked at 2.2.0) — runs on the *captured, post-JS* HTML to (a) power `--auto-selector` main-content identification, (b) enrich manifest metadata (title, author, date, sitename) on **every** capture, as additive fields. It reports article *text*, not a selector, so `capture/prepare.py` locates that text back in the live DOM by vocabulary coverage and isolates the tightest element holding it; the v2 static list is the fallback and `manifest.content_discovery.strategy` records which won. One default is corrected at the seam (docs/09 P3-3): `extract_metadata(extensive=True)` *infers* a publication date for pages that declare none, which a capture tool must not record — the extensive pass is off |
| Rejected | readability-lxml (stale), Defuddle (JS runtime), Jina ReaderLM (model inference for a heuristic's job) |

## 9. Local-format adapters

| | |
|---|---|
| **ADOPT (done, Phase 3)** | **MarkItDown** (MIT, Microsoft, locked at 0.1.7) — CSV/JSON/XML/ZIP/EPUB/Office → Markdown, behind `acquire/markitdown_bridge.py`, the one file that imports it. WebShot keeps: format routing (its own table, not a byte sniff), Markdown→HTML via the existing `Markdown` lib, the image→HTML wrapper, `defusedxml` hardening, the 100 MB guard, **archive safety**, and sanitization |
| Corrections at the seam (docs/09 P3-1) | MarkItDown has no TSV converter — its CSV converter is comma-only — so the bridge re-delimits with the standard library rather than losing the table; and that converter does not escape the Markdown cell delimiter it emits, so a cell containing `\|` or a newline would split its row. Both are fixed on the way in, losslessly. Plain-text formats (JSON/JSONL/XML/YAML/text) come back as the file's own bytes, which is not Markdown, so they are fenced as code rather than reflowed as prose |
| Bonus capability | This adds DOCX/PPTX/XLSX/EPUB/ZIP inputs nearly for free (route → Markdown → HTML → pipeline). DOCX/PPTX/XLSX ship behind the `webshot[office]` extra; EPUB and ZIP need no extra. Every zip-shaped input — including the OOXML formats, which are zip containers — is checked before any converter opens it: expanded size and member count against the 100 MB policy, no member path escaping an extraction root, no symlink members |
| Rejected | Pandoc (GPL, huge external binary), bespoke parsers (deleted) |

## 10. HTML sanitization

| | |
|---|---|
| **ADOPT (done, Phase 3)** | **nh3** (MIT, Rust "ammonia" bindings, locked at 0.3.6) has replaced **bleach**, which is deprecated by its maintainers. ~20× faster; actively maintained. Phase 2 made nh3 the DOM-snapshot sanitizer (the ad-hoc in-browser cleaning JS is gone); Phase 3 ported the source-adapters allowlist and **bleach is gone from the tree and the lock** |
| Two allowlists, one importer | The snapshot keeps form and media records for capture fidelity; a rendered local document keeps Markdown's own output. Both live as data in `webshot/sanitize.py`, the only module that imports nh3 (docs/09 P3-8), so spec §6.5's scheme rule — http(s)/data/mailto, one scheme narrower than v2's bleach call, which also allowed `file:` — has one definition rather than one per stage. The hypothesis property tests in `tests/test_sanitizer.py` run against both |

## 11. MCP server

| | |
|---|---|
| **ADOPT** | **official `mcp` Python SDK** (MIT, modelcontextprotocol/python-sdk; v2 line, `MCPServer` née FastMCP) — stdio transport; tools defined in [04-spec.md](04-spec.md) |
| Reference | docling-mcp, Playwright MCP: patterns for tool naming and doc-reading tools |
| Adopted at | **mcp 2.0.0** (+ `mcp-types` 2.0.0), Phase 4. Pinned `>=2.0,<3` in the **`mcp` extra**, not the core: the SDK depends on starlette/uvicorn/httpx2/sse-starlette/pyjwt/opentelemetry — an HTTP server stack, for transports v3.0 does not ship (docs/09 P4-1) |
| What it replaces | nothing — this is net-new surface. Nothing upstream exposes *capture* over MCP; ADR-0009 is the argument |
| Considered instead | a hand-rolled JSON-RPC stdio loop (rejected: the protocol is the product here, and re-implementing initialize/progress/structured-content is exactly the custom code ADR-0001 exists to avoid); a REST facade (deferred until MCP usage data exists) |
| Known behavior to design around | undeclared tool arguments are ignored rather than rejected, so WebShot adds a strict-arguments middleware — otherwise "no credential-bearing parameters" would be true only because nobody looked (docs/09 P4-1) |

## 12. Schemas, config, CLI, docs, QA tooling

| Slot | Choice | Notes |
|---|---|---|
| Options/schema models | **pydantic v2** (MIT) | manifest + chunk models; `model_json_schema()` exported to `schemas/` |
| Config file | stdlib `tomllib` | no new dep. Loaded **only** from `--config` or `WEBSHOT_CONFIG` — never discovered from the working directory (spec §6.10, docs/09 P4-3); layered over `WEBSHOT_*` and under the flags in `webshot/settings.py` |
| CLI | stdlib **argparse** (retained) | Typer/Click rejected: zero-dep wins for a maintenance-minimal tool |
| XML safety | **defusedxml** (PSF) | all XML parsing |
| Docs site | **mkdocs-material** (MIT) | Adopted at **mkdocs 1.6.1 / mkdocs-material 9.7.7** in Phase 4, pinned `>=1.6,<2` / `>=9.7,<10` in the **`docs` extra** — build-time only, nothing in `src/` imports it. Revisited as the plan asked: still in maintenance mode, and the pin is deliberately below the announced MkDocs 2.0, which drops the plugin system with no migration path. `docs_dir` is the existing `docs/` tree, so the design package is published beside the guide rather than duplicated (docs/09 P4-6). Considered instead: Sphinx (heavier, and reStructuredText for a Markdown repository), plain GitHub rendering (no cross-reference check — `mkdocs build --strict` found three broken links on first run) |
| Lint/format/types | **ruff**, **mypy** (retained) | |
| Tests | **pytest** (migrate from unittest) + pytest-playwright fixtures | golden corpus harness |
| Env/packaging | **uv** + `pyproject.toml` with extras | extras: `office`, `ocr-rapid`, `pdfa`, `mcp`, `dev`, `docs` |
| License gate | **pip-licenses** (MIT) in CI | deny GPL/AGPL/SSPL in installed tree (Ghostscript/veraPDF are external binaries, out of scope of the gate) |
| Automation | Dependabot + pinned versions | upstream churn is the #1 risk (see risk register) |

## 13. Explicitly rejected whole-platform alternatives

| Candidate | Why not |
|---|---|
| **Firecrawl** (AGPL core, cloud-first) | License + hosted-first shape; several capabilities cloud-only |
| **Jina Reader self-host** (Apache-2.0) | A service, not a library; duplicates our capture; no PDF story |
| **ArchiveBox / Linkwarden / Karakeep** | Library-shaped archiving apps, not pipelines; no AI bundle, no synchronized dual output |
| **SingleFile CLI** (AGPL) | Great faithful-snapshot tool; AGPL ⇒ optional *external* companion users may install, never a dependency |
| **Marker / MinerU / PyMuPDF4LLM** | GPL/AGPL/commercial-encumbered; also solve PDF→MD, not our direction of travel |

## License summary of the resulting tree

Direct runtime deps: Playwright, pypdf, pydantic, docling-slim(+extras),
chonkie, Trafilatura, MarkItDown, nh3, Pillow, Markdown, defusedxml,
img2pdf (LGPL), OCRmyPDF (MPL-2.0), mcp; notable transitives via OCRmyPDF:
pikepdf (MPL-2.0), fpdf2, pypdfium2, cryptography, lxml, pdfminer.six,
uharfbuzz, fonttools (and pi-heif until OCRmyPDF 17.12.1 dropped it); via
MarkItDown: magika and **onnxruntime**, which is
weight the default install did not carry before Phase 3 (docs/09 P3-2) — **all
permissive or weak-copyleft; zero GPL/AGPL linked** (gate re-run after Phase 4
with every extra installed, `mcp` and `docs` included: 156 packages, none
denied; **re-run at the end of Phase 5 against the final `uv.lock`: still 156,
still none denied, and the gate now identifies the project itself as
`webshot 3.0.0` rather than 2.2.0 — Phase 5 added no runtime dependency, and
the only lockfile movement is the project's own version becoming dynamic**).
One dependency is
offered under a *choice* of terms — `tld`, reached through Trafilatura, is
"MPL-1.1 OR GPL-2.0-only OR LGPL-2.1-or-later" — and the gate now reads SPDX
disjunctions rather than the whole string, so it reports which acceptable
terms exist instead of refusing a package ADR-0006 permits (docs/09 P3-5). This summary is regenerated from the committed `uv.lock`
by the license-gate job, never maintained by hand. External binaries invoked (user-installed): Chromium, Tesseract,
Ghostscript (via OCRmyPDF), optionally veraPDF/Docker. reportlab and bleach are
**removed** from the tree.

**The `onnxruntime` weight, re-examined in Phase 5 and kept (docs/09 P5-4).**
It cannot be trimmed: `magika~=0.6.1` is an unconditional requirement of every
markitdown 0.1.x release, magika declares `onnxruntime` unconditionally with no
extra to opt out of, and moving MarkItDown behind an extra would take CSV, TSV,
EPUB and ZIP out of the base install — formats spec §1.1 promises there. So the
footprint gate does not assert its absence. What it asserts instead is the line
that matters: no torch, no GPU stack, and no *accelerated* ONNX build
(`onnxruntime-gpu`, `-openvino`, `-directml`, `-training` are denied by name).
The exception is also written to expire — `test_the_onnx_runtime_exception_is_
still_needed` fails the day MarkItDown drops magika, so the allowance cannot
outlive its reason.
