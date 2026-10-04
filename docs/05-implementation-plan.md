# 05 — Implementation plan

Six phases, each independently shippable, each ending with CI green and the
golden corpus passing. Estimates assume one experienced maintainer working
part-time; treat them as sequencing weights, not commitments.

## Git strategy

*As planned and followed for v3.0.0, in the private repository this one was
copied from; none of the branches below exist in this copy. v3.0.0 merged to
`master` and was tagged on 2026-08-21. Since then, work lands on `master`
directly, one pull request per change.*

- `master` — releases only.
- The v2.2 work (`feat/ai-bundle-and-protected-viewer`) merges first; it is the
  v2.2 baseline.
- Long-lived integration branch **`v3`** cut from master after that merge.
- One PR per phase (or per workstream inside a phase) into `v3`; squash-merge;
  every PR keeps the corpus green.
- Final `v3 → master` PR ships v3.0.0 with tag + changelog + migration guide.
- This planning package lives on `plan/v3-oss-composition` and merges into the
  baseline branch so the docs travel with the code.

## Phase 0 — Foundations (≈ 1 week)

Goal: make the repo safe to refactor before changing any behavior.

| # | Task | Notes |
|---|---|---|
| 0.1 | Adopt `pyproject.toml` + uv **with committed `uv.lock`**; src layout (`src/webshot/`); keep top-level `webshot.py` as a shim; raise the pypdf floor to ≥ 6 (the current `>=4.2` floor admits versions lacking the `EmbeddedFile` API the code already uses) | extras: `office`, `ocr-rapid`, `pdfa`, `mcp`, `dev`, `docs` |
| 0.2 | Migrate unittest → pytest; port the 25 existing tests unchanged | pytest-playwright for later phases |
| 0.3 | GitHub Actions (SHA-pinned actions): lint (ruff), types (mypy), tests on {ubuntu, macos} × {3.11, 3.12, 3.13} (3.10 dropped in Phase 1 — docs/09 P1-1); Playwright browser cache; optional tesseract install step | plus pip-licenses gate, **pip-audit/OSV vulnerability scan**, Dependabot config |
| 0.4 | **Freeze the v2 contract**: pydantic models for the *current* manifest/chunk/QA formats; emit JSON Schemas to `schemas/`; snapshot golden bundles from the fixture corpus (timestamps normalized) | this is the parity yardstick for every later phase |
| 0.5 | `webshot doctor` v1 (browser, tesseract, permissions) | trivially extended later |
| 0.6 | **Decompose the webshot.py orchestrator** into 02's module layout (capture/session|prepare|visuals|snapshot, acquire/router, render/pdf, bundle/manifest|publish, config, errors) — behavior-preserving moves only, goldens must stay green | this extraction was previously unowned by any task; Phase 1.3 depends on a separable RENDER stage |
| 0.7 | CONTRIBUTING.md + PR template (bridge-boundary rule, golden re-record policy, security checklist) | pulled forward from Phase 5 — it mitigates R10, so it cannot land last |

**Acceptance:** CI matrix green; golden snapshot job reproduces bundles
byte-identically twice in a row; no behavior change.

## Phase 1 — PDF layer (≈ 1–2 weeks) — biggest LOC reduction

| # | Task | Notes |
|---|---|---|
| 1.1 | Verify/enable `page.pdf(tagged=True, outline=True)`; delete any bookmark logic Chromium now covers | keep custom header/footer templates |
| 1.2 | Protected assembly: page PNGs → **img2pdf** → **`ocrmypdf.ocr()`** (invisible layer, `sidecar=`, multi-core); retain pytesseract TSV pass for `ocr/*.tsv` | deletes the bespoke sandwich |
| 1.3 | Transcript appendix: replace reportlab with an HTML template rendered through the normal RENDER stage, merged via pypdf with bookmarks | **removes reportlab dependency**; note (spike S8): pypdf's merge does not preserve the appendix's structure tree — the merged PDF is not tagged; do not claim otherwise |
| 1.4 | AF embedding consolidated on pypdf `add_attachment` + `AFRelationship=/Data`, MIME types set | |
| 1.5 | `--pdfa` on the protected path via OCRmyPDF **`output_type='pdfa-3'`** (A-3 permits embedded files; the A-2b default forbids them), with the composition order fixed in 02 §PDF deliverable | web path rejected per ADR-0010; acceptance = veraPDF on the **final composed** file |
| 1.6 | `--validate-pdf`: veraPDF via local CLI or `verapdf/cli` Docker; JSON report attached to QA report; **non-blocking** CI job publishing reports per PR | flip to blocking when clean |
| 1.7 | **Record protected-viewer fixtures**: capture sanitized/redacted DOM snapshots of the real SharePoint viewer (structure only, no content/tenant data), store under `tests/fixtures/viewer/`, and build the contract-test harness against them; document the viewer versions covered | closes the gap where R6's mitigation referenced fixtures nothing created |

**Acceptance:** protected fixture round-trips with page-count invariant; PDF
text layer searchable (extract text over each page, assert OCR terms found);
appendix present with working bookmarks and back-links (not claimed tagged —
spike S8); **veraPDF conformance check runs against the final composed
`--pdfa` output**, and the composition order (OCR → appendix → embeds) is
exercised, not just the OCR step in isolation; reportlab gone;
`protected_viewer.py` shrinks ≈ 1,163 → ≈ 500 LOC.

## Phase 2 — Extraction swap (≈ 2 weeks) — riskiest phase

| # | Task | Notes |
|---|---|---|
| 2.1 | **nh3 adoption moves here** (Phase 2's input is *sanitized* HTML, so sanitization cannot wait for Phase 3): nh3 replaces the ad-hoc DOM-cleaning JS for the snapshot. Then `extract/docling_bridge.py`: sanitized HTML → DoclingDocument via **docling-slim[format-html]**, using `HTMLDocumentBackend` directly; map harvested visuals to PictureItems; attach OCR text/conf/lang + captions as annotations | the one file importing docling |
| 2.2 | Exports via docling serializers (md/txt/html/lossless JSON/doctags); tables → CSV from TableItems | |
| 2.3 | Chunking via **HierarchicalChunker + chonkie size-splitting** (gate pre-resolved by spike, docs/09 S4; ADR-0008); HybridChunker behind an extra | chunk records per spec §2 |
| 2.4 | **Parity harness**: side-by-side v2-vs-v3 bundle diff over the corpus — text coverage, headings, tables, links, asset counts; thresholded report | goes in `tools/parity/` |
| 2.5 | `--legacy-bundle` flag emitting v2 `content.json`/`chunks.jsonl` alongside v3 for one release; migration guide (`docs/migration-v2-to-v3.md`) | |

**Acceptance:** parity report ≥ agreed thresholds (default: 100% headings/tables/
links, ≥ 99% text token coverage) on the corpus; chunks carry `doc_items`
provenance; `ai_artifacts.py` deleted (≈ 771 → ≈ 290 across bridge+exports+chunks).

## Phase 3 — Adapters, discovery, sanitization (≈ 1 week)

| # | Task | Notes |
|---|---|---|
| 3.1 | Route non-HTML local formats through **MarkItDown** → Markdown → existing MD→HTML path; keep 100 MB guard + defusedxml; delete bespoke parsers | `source_adapters.py` ≈ 293 → ≈ 80 |
| 3.2 | New input formats behind `[office]` extra: DOCX/PPTX/XLSX/EPUB/ZIP | free capability, fixture-tested |
| 3.3 | Port the **source-adapters bleach allowlist** (the only allowlist v2 actually has — the DOM snapshot was cleaned by ad-hoc JS, replaced by nh3 in 2.1) to nh3 and delete bleach; property tests for the sanitizer | bleach is deprecated upstream |
| 3.4 | `--auto-selector` + metadata via **Trafilatura** on captured HTML; static selector list kept as fallback; manifest records which path won | metadata enrichment runs on every capture, as additive manifest fields |
| 3.5 | **`OcrEngine` protocol formalized** in `ocr/engine.py`; **RapidOCR** behind `[ocr-rapid]`; `--ocr-engine tesseract\|rapid` wired per spec §1.1, including `--ocr-psm` warned-and-ignored under `rapid` | *Plan amendment, recorded as docs/09 P3-7.* 02's module map, 03 §5 and 04 §1.1 all specified this work and no task owned it — the same gap P0-1 found for the orchestrator |

**Acceptance:** all fixture formats round-trip, including the Markdown
relative-asset resolution fixture; sanitizer property tests pass on **both**
allowlists; XXE and archive-safety tests pass; office-format smoke fixtures
pass under the extra and the extra does not leak into the default install;
`--ocr-engine rapid` produces a valid bundle on an OCR fixture; bleach absent
from the tree **and the lock**.

## Phase 4 — Interfaces (≈ 1 week)

Ordered 4.4 first: `[mcp]` settings live in the config file, so the settings
layer has to exist before the server that reads it.

| # | Task | Notes |
|---|---|---|
| 4.4 | **`--config webshot.toml` (stdlib `tomllib`) and `WEBSHOT_*`**, resolved into the options model with spec §1.1's precedence: flags > env > file > defaults. The file is loaded **only** from an explicit path (`--config`, or `WEBSHOT_CONFIG`) — never by working-directory discovery, which would let an untrusted directory inject settings (spec §6.10) | *Plan amendment, recorded as docs/09 P4-3.* 04 §1.1 specified this and no plan row owned it — the same gap P0-1 found for the orchestrator and P3-7 for the OCR seam. Ships before 4.1 because `[mcp]` lives in the file |
| 4.1 | `webshot mcp` on the official `mcp` SDK; tools per spec §1.3; integration-tested with an MCP client harness | Guardrails are MUSTs with a failing-case test each (spec §6.8a–f, amended in P4-4): roots confinement, server-chosen output root, default-deny private networks with a `final_url` re-check, no credential-bearing parameters, interactive-profile refusal, serialized captures. Tool names and schemas are frozen after v3.0 |
| 4.2 | Docs site: mkdocs-material — quickstart, CLI reference **generated from the argparse parser with a drift check**, bundle-format-3 spec, MCP guide (tool contract, roots and network policy, and the statement that returned page content is untrusted data), security model, migration guide, capture-ethics policy, doctor/troubleshooting | `docs_dir` is the existing `docs/` tree, so the design package is published alongside the guide rather than duplicated. CI builds with `--strict` **blocking**; the Pages deploy workflow exists but is not enabled this phase (it now runs on every push to `master`, and deploys once the repository is public and Pages is set to GitHub Actions) |
| 4.3 | QA report v2 schema finalized; `webshot schema manifest\|chunk\|qa-report` complete; the schema-agreement check extended to `qa-report` | |

**Acceptance:** the MCP client harness is green in CI **including a denied case
for every guardrail**; network-policy tests green (default-deny private, opt-in
works, redirect-to-internal refused); config precedence and explicit-path-only
proven by tests; the docs build is blocking and green; `webshot schema` covers
all three named contracts; both golden profiles green.

## Phase 5 — Hardening & release (≈ 1 week)

| # | Task | Notes |
|---|---|---|
| 5.1 | Error taxonomy → exit codes audit; every failure path exercised in tests | **Done.** All ten codes covered — 0, 2, 3, 4, 6, 8, 9 by a real invocation, 5 and 7 through the real classifying function with the fault injected. `--require-ocr` implemented (documented in §1.1 since Phase 0, never built). A failing run now writes its QA report; before this, `exit_code` was 0 in every report that existed. Boundaries between 2/4 and 3/5 written into spec §3 (docs/09 P5-1, P5-2) |
| 5.2 | Perf smoke budget in CI; `--max-assets` and scroll-limit behavior re-verified | **Done.** `tools/perf/smoke.py`, non-blocking and trend-tracked. The baseline is **recorded on the CI runner** and carries its environment; a run on other hardware publishes its number without judging it (docs/09 P5-5). `--max-assets` and the scroll limit stay covered by the `article-capped-assets` golden and the scroll-limit warning test |
| 5.3 | SECURITY.md, CHANGELOG.md (CONTRIBUTING landed in 0.7); release workflow: build + tag, schemas + veraPDF report attached | **Done.** `release.yml` verifies before it publishes and publishes nothing until the built wheel has passed 5.6. The Trusted-Publishing step is **written and disabled** — see 5.4. README audited against v3 reality in the same pass |
| 5.4 | **Name decision executed** (see Open questions) | **Resolved 2026-08-21 (owner decision): keep the name `webshot`, and do not publish to PyPI yet.** The CRAN collision (`webshot`, `webshot2`) and shot-scraper's adjacent niche are documented in the README and CHANGELOG; the release is a GitHub release. The rename-then-publish checklist lives in the disabled `pypi` job in `release.yml` |
| 5.5 | Footprint hardening: the default install's ONNX runtime (promoted from P3-2) | **Done, and it stays.** No route removes it that does not cost more than 75 MB. The gate now denies the accelerated ONNX builds by name and the exception is written to expire (docs/09 P5-4) |
| 5.6 | **Release gate (plan amendment, docs/09 P5-6):** build the wheel, install *only that artifact* into an empty virtualenv, and from it run a real capture, `webshot schema manifest` and `webshot doctor` | **Done.** `tools/release/wheel_check.py`, and a step in `release.yml` before anything is published. The plan built artifacts and never used one; every other check in this repository runs against the source tree, which is not what a user has |
| 5.7 | v3.0.0: final parity run, veraPDF report published, tag | **Done 2026-08-21.** Tagged on `master` once the `v3` branch had merged, and released on GitHub from the private repository; that tag and its assets are not in this copy |

*Two specification MUSTs with no plan row were found by the release audit and
built here rather than deferred: §5.1's per-output lockfile and §7's retirement
of the legacy entry-point shims (docs/09 P5-7).*

## LOC budget

**The canonical per-module budgets live in 02-architecture.md §Module map;
this rollup derives from that table** (fix for an earlier contradiction where
the two documents disagreed). G1 is measured as: sum of modules marked
custom/custom-core in 02.

| Rollup | v2.2 | v3 target |
|---|---:|---:|
| Total (custom + bridges) | 3,343 | ≈ 6,540 |
| Custom, non-bridge | 3,343 | ≈ 5,054 |
| — of which core pipeline logic (capture, visuals, protected viewer, render) | — | ≈ 1,350 |
| — of which extracted infra (cli/pipeline/config/settings/report/errors/manifest/publish/doctor) | — | ≈ 2,485 |
| — of which net-new (MCP server, minus its SDK bridge) | 0 | ≈ 1,219 |
| Bridge/glue around upstream libraries | 0 | ≈ 1,486 |

*Revised in Phase 1 from 02's canonical table (docs/09 P1-7) — accounting plus
the veraPDF gate, not retained problem-solving — and again in Phase 3 (P3-6, P3-8),
where +540 is mostly real: five new input formats, the archive-safety and XML
guards, and the OCR engine seam. The bespoke format parsers are gone, which is
what G1 measures; what replaced them is routing, guards, and presentation.
Revised once more in Phase 4 (P4-2, P4-3, P4-5, P4-9): +1,915 for the MCP
server's guardrails, the settings layer, and the QA report — the interfaces this
phase exists to build, none of which duplicates anything upstream provides. It is
the biggest revision in the project, and its lesson is that a security boundary
costs more than the surface it guards. Note how little of it is *bridge*: 296 of
the MCP server's 1,515 lines import the SDK, and the rest is WebShot's own rules
— which is the honest shape of adopting a protocol rather than a library.*

**Measured, not budgeted (added 2026-10-03).** The figures above are the plan's
budgets. Counted in code lines (blank lines, comments and docstrings excluded,
the method of docs/09 P1-7) over `src/webshot/`, with each module classified by
02's *Kind* column and modules 02 does not list counted as custom:

| | v3 target (this table) | v3.0.0, 2026-08-21 | `master`, 2026-10-03 |
|---|---:|---:|---:|
| Total | ≈ 6,540 | 7,633 | 15,182 |
| Custom, non-bridge | ≈ 5,054 | 6,185 | 12,630 |
| Bridge/glue around upstream libraries | ≈ 1,486 | 1,448 | 2,552 |

So v3.0.0 came in about a fifth over the revised custom budget, and well over
01's original G1 figure of ≈ 2,300. Everything since 3.0.0 is new capability
rather than a rewrite: embedded-video capture (+2,301: the walkthrough and
caption artifacts, the Guidde bridge and the video appendix), the `webshot
journey` walker (+1,837), the print-fidelity work of docs/09 P16 (+1,657: the
clipping check, frames, shadow roots, scrolling boxes and ligatures) and
Windows support (+476: profile ACLs and onnxruntime's telemetry switch). The
part of G1 this rollup cannot show is the one that mattered: which commodity
problems WebShot stopped solving by hand.

The headline is not raw LOC — it is *which* lines remain: after v3, no custom
code implements OCR layering, semantic extraction, chunk splitting, format
parsing, or sanitization. Bespoke problem-solving code deleted ≈ 1,350
(protected sandwich ≈ 660, extraction/chunking ≈ 480, format parsers ≈ 210).

## Dependency change summary

Added (direct): docling-slim(+extras), chonkie, **ocrmypdf (17.x, Phase 1)**,
**img2pdf (0.6.x, Phase 1)**, markitdown, trafilatura, nh3, pydantic,
mcp (extra), pytest (dev). OCRmyPDF is a core dependency rather than the
`pdfa` extra, and it raises the Python floor to 3.11 (docs/09 P1-1, P1-2).
Arrives transitively via OCRmyPDF: pikepdf, fpdf2, pypdfium2 — tracked in the
lockfile and license gate like any other dependency. Removed: bleach,
reportlab. NOT added: pytesseract — v2 never used it (tesseract is invoked as
a subprocess; that proven code is retained). Unchanged: playwright, pypdf,
Markdown, Pillow. The authoritative list is the committed `uv.lock`; 03's
license summary is regenerated from it, not maintained by hand.

## Open questions (owner decisions, none blocking Phase 0–1)

1. ~~**Rename before PyPI?**~~ **RESOLVED 2026-08-21 (owner decision): keep
   `webshot`, and defer PyPI publication.** The collision is real — `webshot`
   and `webshot2` on CRAN, with `shot-scraper` in the adjacent Python niche —
   and publishing under this name before deciding how to resolve it would make
   it permanent. v3.0.0 is a GitHub release; the README and CHANGELOG state the
   collision and the deferral, and the four steps that would enable publication
   (the rename first, then Trusted Publishing) are written above the disabled
   `pypi` job in `.github/workflows/release.yml`.
2. ~~Chunker default~~ **RESOLVED 2026-08-19** by spike (docs/09 S4):
   HierarchicalChunker + chonkie; HybridChunker optional (ADR-0008).
3. ~~Web-path PDF/A~~ **RESOLVED 2026-08-19** by spike (docs/09 S7): tags are
   stripped — PDF/A is protected-path-only in v3 (ADR-0010).
4. **OpenDataLoader adoption** for tagging image-based PDFs — re-evaluate when
   its open-source tagging path stabilizes; roadmap item, not v3.
5. ~~WebShot's own license~~ **RESOLVED 2026-08-19 (owner decision):
   Apache-2.0.** LICENSE file added (canonical text), `pyproject.toml`
   declares `license = "Apache-2.0"` + `license-files` (PEP 639, hatchling
   ≥1.26), and the license gate should now identify the package. Chosen for
   the explicit patent grant and alignment with the adopted stack
   (Playwright/Trafilatura norm).
