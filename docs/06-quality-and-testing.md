# 06 — Quality & testing strategy

## Test pyramid

| Layer | Scope | Tooling | Network |
|---|---|---|---|
| Unit | manifest/chunk models, sanitizer allowlist, adapters routing, error taxonomy, checksum/atomic publish | pytest | none |
| Bridge contract | each bridge module against its upstream (docling_bridge, ocr engines, ocrmypdf assembly, markitdown routing, trafilatura discovery) | pytest, real libraries, tiny inputs | none |
| Integration | full pipeline against a **local fixture HTTP server** (static + JS-mutating + lazy-load + canvas + iframe pages) | pytest-playwright | localhost only |
| Golden corpus | byte-stable bundles + PDF text-layer assertions for every fixture | snapshot harness from Phase 0 | localhost only |
| Parity (Phase 2–3 only) | v2 vs v3 bundle equivalence report | `tools/parity/` | localhost only |
| Validation gates | veraPDF on produced PDFs; JSON Schema validation of every manifest/chunk/QA artifact CI produces | veraPDF Docker, jsonschema | none |

Principles: no test touches the public internet; timestamps and tool-version
strings normalized before snapshot comparison; every bug fix lands with a
fixture reproducing it.

## Fixture corpus (extends `tests/fixtures/`)

Existing seven fixtures stay. Add: a lazy-load/infinite-scroll page (bounded),
a canvas-chart page (rasterization + OCR), an iframe-embedding page, a
CSS-background-image page, a JS-only SPA shell (content absent from static
HTML — the case that proves the browser matters), an RTL/mixed-language page,
a password-form page (redaction test — **created in Phase 2's corpus audit**
as `form_page.html`, together with `structures_page.html` (definition lists,
blockquote, code, ordered list) and `media_page.html` (video/audio with
`<track>` captions): the fidelity-mapping types had no fixture, and their v2
baselines had to be recorded before the extraction swap removed the v2 path;
the form fixture immediately caught a real §6.2 leak — the aria snapshot
reported password values), a simulated protected-viewer document
(**created in Phase 0**: `tests/fixtures/viewer/page-*.png`, two page images
written for this repository — the earlier claim that one already existed was
wrong, see docs/09 P0-3), the protected-viewer **DOM** fixtures
(**created in Phase 1**: `tests/fixtures/viewer/dom/*.html`, authored from the
accessibility-tree contract rather than exported from a tenant — docs/09 P1-6),
and per-format office fixtures (docx/pptx/xlsx/epub/zip) for the
`[office]` extra.

## The golden harness

- `tools/golden/record.py` — captures corpus → normalizes (timestamps, version
  strings, non-deterministic IDs) → stores under `tests/golden/`.
- `tools/golden/check.py` — re-captures and diffs; failure prints a readable
  per-file report (missing headings, changed text spans, asset count drift).
- **Raster policy:** golden equality is byte-level for text/JSON artifacts
  only; raster assets are compared by dimensions + format + presence (pixel
  bytes legitimately drift across Chromium/Tesseract upgrades). SHA-256 of
  rasters is asserted within a single run (manifest ↔ file), not across runs.
- **Comparison profiles (added in Phase 0, docs/09 P0-2):** recognized text is
  environment-dependent for the same reason pixels are, and it reaches
  `content.*`, `chunks.jsonl`, the PDF text layer, and every count derived from
  them. `check.py` therefore compares on one of two profiles and prints which:
  **strict** (everything byte-compared, including OCR text) when the current
  environment matches `tests/golden/environment.json`, and **portable** (OCR
  text, OCR-derived counts, and the browser build masked) when Tesseract,
  Playwright, or the platform differ. CI runs portable by construction.
- **Sentinels:** each corpus case names strings that exist *only* inside a
  raster (a canvas chart, a scanned page). They must appear in the recognized
  text on both profiles — that is what keeps OCR proven when its exact words
  are no longer compared. Masked numbers stay numbers (`-1`), so a golden still
  validates against the frozen contract models.
- **OCR-derived PDF text (added in Phase 1, docs/09 P1-8):** on the protected
  path the PDF's text layer is OCRmyPDF's own reading of the page images, which
  need not agree line-for-line with the TSV pass the bundle publishes. A case
  declares `ocr_pdf_text`, and the portable profile then blanks the page bodies
  of `pdf-text.txt` and the text-layer sidecar while keeping the page markers —
  page count and order stay compared, the words stay proven by sentinels. The
  appendix's page count is masked for the same reason (Chromium paginates it
  from recognized text); the page-count invariant is asserted in code instead.
- **Structural assertions, not recorded values (added in Phase 1):** each case
  declares whether its PDF must be tagged (`StructTreeRoot` + `MarkInfo`) and
  bookmarked, and both `check.py` and `record.py` verify it against the produced
  file. `record.py` refuses to record a case whose PDF is not what the case
  says it is, so the assertion cannot be re-recorded away.
- **Flake policy:** Playwright jobs auto-retry once; a second failure is
  real. A test that flakes twice in a week is quarantined via marker with an
  issue link — never deleted silently.
- **Re-baseline procedure:** pinned-browser upgrades re-record goldens in a
  dedicated PR containing only the upgrade + re-recording, with the parity
  report attached; mixing re-records into feature PRs is prohibited (matches
  the review checklist).
- PDF assertions: pypdf page count, outline titles, per-page extracted text
  contains expected sentinel strings (including OCR sentinels rendered only
  inside images/canvases on fixture pages), attachment names +
  AFRelationship present.

## The parity harness (Phase 2–3)

Compares a v2 bundle and a v3 bundle of the same fixture: token-level text
coverage (target ≥ 99%), exact-match sets for headings/tables/links/assets
(target 100%, measured on the visible-content basis defined by the
capture snapshot — v2's computed-style visibility filter moves into CAPTURE,
so both sides see the same DOM), **plus the fidelity types docling does not
model: form-field records (including the redacted-password records), definition
lists, and embedded-media/track records — each must appear in the v3 bundle's
assets.json/annotations or be explicitly deprecated in 02's fidelity mapping**,
and chunk coverage (every v3 chunk maps to ≥1 DocItem; concatenated chunks
cover the document text). Output: markdown report attached to the PR.
Thresholds live in one TOML so loosening them is a visible diff.

The `assets` metric identifies a visual asset by what the page authored —
kind, pixel size and alt text — and **not** by the digest of its raster. That
digest moves with the fonts that drew the text inside it: a macOS font update
changed every text-bearing asset in the corpus and scored this metric at 0% on
nine cases where nothing about the assets had changed (docs/09 P7-14). The
threshold is still 1.0 and the `strip-asset` mutations still fail it, so a lost
asset is still caught; what a re-render no longer does is read as a
regression. Within a single capture the published digest is still checked —
`asset_digest_failures` asserts it against the raster on disk.

## CI (GitHub Actions)

| Job | Matrix | Gate |
|---|---|---|
| lint+types | ubuntu, 3.12 | blocking |
| tests | {ubuntu, macos} × {3.11, 3.12, 3.13} | blocking |
| golden corpus | **macos** 3.12 (chromium cached, tesseract via brew) — the recording platform, because Chromium's text layout is platform-dependent (docs/09 P0-6) | blocking |
| license audit (pip-licenses deny GPL/AGPL/SSPL, regenerates 03's summary from `uv.lock`) | ubuntu | blocking |
| vulnerability scan (pip-audit/OSV) | ubuntu | blocking |
| CLI-contract check (every `build_parser()` option present in 04's table, and the table invents none) | runs inside `pytest` (`tests/test_cli_contract.py`) — blocking, and available locally |
| windows smoke (install, unit tests, fixture article without OCR; non-protected; two captures into one `--auth-profile`, then every entry's owner and ACL read back by `tools/ci/profile_acl.py` — docs/09 P10-25) | windows, weekly | blocks no pull request, but a failure fails its own run, and the job summary lists every failure and every skip (docs/09 P10-24). Known Windows limitations: 04 §4.1 |
| veraPDF report (`tools/verapdf/check.py`: assembles the protected fixture with `--pdfa` and validates the **final composed** file — OCR layer, appendix, bookmarks, back-links, embedded files) | ubuntu (Docker) | **non-blocking → blocking** once baseline is clean. Baseline as of Phase 1: PDF/A-3B conformant, 146 rules passed (docs/09 P1-4) |
| schema check (`schemas/` matches the contract models; `webshot schema` from Phase 4.3) | runs inside `pytest` (`tests/test_contracts.py`) — blocking, and available locally |
| perf smoke (`tools/perf/smoke.py`: fixture article, one discarded warm-up then the median of 3 runs, budget = 3× the recorded baseline median; the baseline is **recorded on the CI runner** and carries the environment it was taken on, so a run on other hardware publishes its number without judging it; re-recorded only with a pinned-browser or Playwright upgrade, through the workflow's `record_perf_baseline` input — with one exception, the first hosted runs, because the baseline in the tree predates GitHub-hosted runners: see [Runners](#runners)) | ubuntu | non-blocking, trend-tracked |
| docs build | ubuntu | blocking (Phase 4+) |
| release (`release.yml`, on tag: build sdist+wheel, run the suite with every extra the tests need, attach wheel + sdist + JSON schemas + veraPDF report to a GitHub release; `workflow_dispatch` dry-runs it without publishing; the PyPI step is written and disabled) | on tag | manual approval (the `release` environment; add required reviewers there to arm it) — rehearsed first with a `workflow_dispatch` dry run |

## Runners

Every job runs on a GitHub-hosted runner; the repository registers none of its
own.

| Runner | Jobs |
|---|---|
| `ubuntu-latest` | lint + types, the Linux legs of `tests`, docs site, license gate, vulnerability scan, perf smoke, veraPDF, both `Claude review` jobs, `release`, and the Pages `docs` workflow |
| `macos-latest` | golden corpus, parity, the macOS legs of `tests` |
| `windows-latest` | windows smoke, weekly |

Each job installs what it uses on top of the runner image rather than assuming
it: Chromium through Playwright (cached against `uv.lock`), Tesseract from apt
or Homebrew, Ghostscript and the `verapdf/cli` container where a job builds or
validates a PDF/A, and the uv cache through `setup-uv`. Two things are taken
from the image: Docker on Ubuntu, for veraPDF, and `gh`, which the `Claude
review` job checks for before it spends a review, because a review without it
once failed and still reported success (docs/09 P10-18).

**The goldens were not recorded on a hosted runner.** The golden corpus and
parity compare against recordings made on a maintainer's Mac. A hosted macOS
image has its own macOS release, Tesseract build and system fonts, so
`check.py` fingerprints the difference and compares on the portable profile.
That profile masks recognized text and raster digests; it does not mask layout.
If a hosted image's fonts paginate a fixture differently, the job fails for a
reason that is not a regression, and settling it is a recording decision with a
written reason (CONTRIBUTING rule 2), never a re-record to make it pass. The
longer-term answer is the one docs/09 P0-6 names: a second recorded
environment.

**The perf baseline predates hosted runners.** `tools/perf/baseline.json` was
recorded in a Linux/x86_64 container on other hardware, before CI moved to
GitHub-hosted runners. `Environment.comparable_to` compares the operating
system, the architecture and `RUNNER_OS`, and a hosted Ubuntu runner matches
all three, so the first hosted runs are *judged* against a median that hardware
never produced: a pass is weaker evidence than it looks, and an over-budget run
may be the hardware rather than the code. Treat those runs as calibration. If
the hosted median sits well away from the recorded 6.35s, re-record it through
the `record_perf_baseline` input, in a pull request of its own that says why —
the one re-recording the perf row above allows outside a browser upgrade.

**Pull requests from forks** get the full CI. `pull_request` gives a fork's
run a read-only token and no secrets, and no CI job needs either. No workflow
here uses `pull_request_target`, which would run with this repository's secrets
against code the fork wrote; keep it that way. `Claude review` does need a
secret, so a gate job in front of it skips forks — and bots, drafts, and any
run the secret did not reach — and writes the reason to the run summary, so the
review shows as `skipped` with its cause beside it rather than as a bare skip
or a green that reviewed nothing.

## Review checklist additions (PR template)

- [ ] No upstream types leak across a bridge boundary
- [ ] New failure path mapped to an exit code + QA-report entry
- [ ] Golden corpus updated deliberately (diff explained in PR body), never blindly re-recorded
- [ ] No new runtime dependency without a components-doc entry + license check
- [ ] Security invariants (spec §6) untouched or strengthened
