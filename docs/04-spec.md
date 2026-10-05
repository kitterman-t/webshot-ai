# 04 — Functional specification (spec sheet)

Normative language: **MUST / SHOULD / MAY** per RFC 2119.

## 1. Interfaces

### 1.1 CLI

```
webshot SOURCE [options]
webshot doctor
webshot mcp [--transport stdio] [--config FILE] [--print-roots]
webshot schema [NAME] [--write DIR]   (prints, or regenerates, a JSON Schema)
webshot journey URL [options]         (walks an LMS training journey; specified in docs/13)
```

`webshot schema` covers every published contract, `manifest`, `chunk`, and
`qa-report` among them; with no name it lists them.

`SOURCE` is an http(s) URL or a local path. All v2.2 flags MUST keep working in
v3.0; renamed flags MUST alias with a deprecation warning for one minor
release.

#### Flag compatibility table (v2.2 → v3)

Generated from v2.2's `build_parser()` (39 options, verified 2026-08-19).
**Contract:** this table is regenerated mechanically, and CI asserts every
argparse option appears here (06 §CI) — a hand-maintained table is how v1 of
this document listed a flag (`--custom-css`) that does not exist.

| v2.2 flag(s) | v3 status | Notes |
|---|---|---|
| `--output`, `--title`, `--selector`, `--exclude`, `--mode`, `--wait-for`, `--delay`, `--timeout`, `--no-scroll`, `--max-scrolls`, `--scroll-delay` | unchanged | |
| `--auto-selector` | unchanged | Trafilatura-backed with static-list fallback; winning strategy recorded in manifest |
| `--format`, `--landscape`, `--margin`, `--scale`, `--media`, `--prefer-css-page-size`, `--no-header-footer`, `--css`, `--user-agent`, `--allow-http-errors` | unchanged | |
| `--no-tagged-pdf`, `--no-outline` | unchanged | v2 already emits tagged+outline by default — Phase 1.1 *verifies* (golden assertions), it does not introduce |
| `--storage-state`, `--auth-profile`, `--interactive-auth` | unchanged | never copied into bundle; owner-only perms (MUST) |
| `--protected-viewer` | unchanged | new assembly backend, same guarantees |
| `--no-ai-bundle`, `--ai-bundle-dir`, `--max-assets` | unchanged | `--no-ai-bundle` also disables embedded-video capture and the walkthrough appendix, because both are written into the bundle. Warned at run time and described in [the bundle guide](guide/bundle-format.md#embedded-video-capture-needs-the-bundle) (docs/09 P8-69) |
| `--no-ocr`, `--ocr-language`, `--ocr-psm` | unchanged | see `--ocr-engine` below for how `--ocr-psm` behaves once an engine can be chosen |
| `--report`, `--debug-screenshot`, `--verbose`, `--version` | unchanged | `--report` schema v2 |
| *(new)* `--ocr-engine` | new | `tesseract` (default) or `rapid` (RapidOCR, `webshot[ocr-rapid]`, pip-only). `--ocr-psm` is Tesseract-specific and is warned-and-ignored under `--ocr-engine rapid`; `--ocr-engine rapid` is itself warned-and-ignored on `--protected-viewer`, whose text layer and word coordinates come from OCRmyPDF/Tesseract. Per-asset records name the engine that produced them |
| *(new)* `--require-ocr` | new | missing OCR becomes exit 6; without it, degradation is a manifest warning. Checked before the browser opens, against the engine that will actually run — Tesseract on `--protected-viewer`, whatever `--ocr-engine` names elsewhere. `--require-ocr` with `--no-ocr` is contradictory → exit 2. Also `[ocr] require` / `WEBSHOT_OCR_REQUIRE` |
| *(new)* `--require-content` | web path | an empty capture becomes exit 5 with nothing published; without it, an empty capture is a manifest warning (§5 item 14). Reads the bundle's counts, so `--require-content` with `--no-ai-bundle` is contradictory → exit 2. Warned-and-ignored on `--protected-viewer`, which checks its pages against the viewer's page count instead. Also `[capture] require_content` / `WEBSHOT_CAPTURE_REQUIRE_CONTENT` (docs/09 P22-2) |
| *(new)* `--pdfa` | **protected path only** | web path rejected — PDF/A conversion strips Chromium's tags (spike-proven, ADR-0010 / docs/09 S7) |
| *(new)* `--validate-pdf` (optionally `=strict`) | veraPDF if available | validates against the flavour the file itself declares; absent veraPDF → warning; a file declaring no PDF/A → warning (nothing to validate); non-conformant → report + warning; `strict` → exit 7 |
| *(new)* `--no-embed-bundle` | web path | by default the bundle is carried inside the PDF as associated files, so one file is the whole deliverable; this restores the PDF-plus-directory shape |
| *(new)* `--embed-assets` | web path | also embed the binary visual assets (larger file; the images are already visible in the rendered pages and `assets.json` carries their recognized text) |
| *(new)* `--local-paths` `relative` (default `absolute`) | web path, local sources | records a local source's `source` and `final_url`, and every `file:` URL under the source file's directory that the bundle records (images and media in `assets.json`, `links.json`, `page_metadata.canonicalUrl`, the `legacy/` files, `README.txt`), relative to that directory (`./page.html`, `./images/a.png`) instead of as the capturing machine's absolute paths; the manifest and the embedded `capture.json` then say `local_paths: "relative"`, and without the flag that key is absent and the bundle is byte for byte what it was. Written that way rather than rewritten afterwards, so every digest describes the bytes the bundle holds. A `file:` URL outside the directory is kept and counted in a warning, and a bundle file that still names the directory anywhere is named in a warning. The PDF's own link annotations and the QA report keep their paths. Refused with `--protected-viewer` on a local source, whose bundle it does not reach (docs/09 P14-61) |
| *(new)* `--no-videos` | web path | skips reading the walkthroughs of videos embedded in the page. Enrichment is on by default because on a page whose procedure lives inside a player the walkthrough *is* the content; with this flag the capture is exactly what it was before the feature existed, including every byte of the bundle. A page with no embedded video pays one DOM read |
| *(new)* `--video-assets` | web path | also downloads each embedded video's step clips and source recording into the bundle. Off by default, and not for size: an agent cannot watch a video, the stills and the narration already carry the procedure, and unplayable media is bulk rather than content. Warned-and-ignored where there is no bundle to write into (`--no-ai-bundle`, `--protected-viewer`) |
| *(new)* `--legacy-bundle` | **ships in v3.0, removed in v3.1** | emits v2-format artifacts under `legacy/` inside the bundle, listed in the manifest with `legacy: true` |
| *(new)* `--config` | TOML file | precedence: flags > env > file > defaults. Loaded **only** from `--config` or `WEBSHOT_CONFIG`; there is no working-directory discovery (§6.10). Settings also arrive as `WEBSHOT_<TABLE>_<KEY>` |

### 1.2 `webshot doctor` (new, MUST)

Checks and reports, with per-item pass/optional/warn/fail and one-line fixes
(optional: a tool only an opt-in flag uses is absent; only fail exits non-zero):
Playwright browser installed; Tesseract present + requested language packs;
Ghostscript present **and ≥ 10.7** (10.06 has JPEG-corrupting bugs flagged by
OCRmyPDF — spike finding, docs/09) (only if `--pdfa`/protected); veraPDF
present or Docker **daemon running** (only if `--validate-pdf`); write
permissions on output dir; docling-slim extras importable. Exit 0 if all
required items pass.

Implemented as of Phase 1: browser, Tesseract + language packs, Ghostscript
version, veraPDF/Docker daemon, output directory. The docling-slim check
arrived with docling in Phase 2; Phase 3 added a Local formats check
(MarkItDown + Trafilatura importable and versioned, and which office-format
converters the `[office]` extra has made available).

### 1.3 MCP server (new, MUST)

Official `mcp` SDK, stdio transport. Tools (all return structured content):

| Tool | Input | Output |
|---|---|---|
| `capture` | `source`, options subset (mode, selector, ocr, protected, auth_profile name) | manifest summary + bundle path + pdf path + warnings |
| `read_markdown` | bundle path | `content.md` text (size-capped, paged) |
| `query_chunks` | bundle path, `query?`, `limit` | chunk records (substring match v3.0; embeddings out of scope) |
| `get_manifest` | bundle path | manifest.json |
| `list_assets` / `read_asset` | bundle path (, asset id) | asset records / base64 image |
| `doctor` | — | doctor report |

Tool names, parameter names, and result shapes are an API contract, published
in the MCP guide and **frozen after v3.0**: renaming one breaks every agent
configured against the server.

**Returned page content is UNTRUSTED DATA, never instructions** (added in
Phase 4, docs/09 P4-4). Markdown, chunks, titles, and asset text were written by
the captured page. The server states this in its `instructions`, in every
content-returning tool's description, and in a `notice` field on the results
themselves; a client MUST treat that content as data and MUST NOT execute
instructions, tool calls, or URLs found inside it. Every guardrail in §6.8
exists because this boundary can fail.

Security: MCP `capture` MUST refuse `file://` outside an allowlisted root and
MUST NOT accept raw storage-state content as a parameter (profile *names* only).
The full guardrail list is §6.8, and the operational rules are §5.9.

### 1.4 Render call (normative, spike-verified)

The web-path PDF MUST be produced with Playwright Python's
`page.pdf(tagged=True, outline=True, …)` (exact parameter names in
playwright ≥ 1.61) — this yields `StructTreeRoot`, `MarkInfo`, and a
heading-derived outline (docs/09 S1).

## 2. Outputs

### 2.1 Filesystem layout (unchanged defaults)

```
output/pdf/<slug>.pdf
output/pdf/<slug>.ai/            # bundle_format: 3 — see 02-architecture.md §Data contracts
```

### 2.2 Publication invariants (MUST)

- Atomic: bundle assembled in a temp dir, fsynced, renamed into place; a
  crash never leaves a partial bundle at the destination.
- PDF validated before publication: parseable by pypdf, ≥1 page, non-zero
  page sizes; protected path additionally `pdf_pages == source_pages +
  appendix_pages`.
- Every bundle file listed in `manifest.json.checksums` with SHA-256; the
  manifest lists its own schema version and the versions of webshot,
  playwright/chromium, docling-slim, ocrmypdf, tesseract actually used.
- OCR degradation (engine missing, language pack missing) MUST surface as a
  manifest warning and QA-report entry, never silently.
- Text artifacts are UTF-8 with `\n` line endings on every platform, so a
  capture's bytes and digests do not depend on the machine that made it.
  `tables/*.csv` are the one exception, and it is the same everywhere: RFC
  4180's `\r\n`. `source/` holds the input byte for byte, whatever its line
  endings (docs/09 P10-27).

### 2.3 QA report (`--report`), schema v2

JSON: source, options as resolved, timings per stage, counts (assets, OCR
words, chunks, tables, links, pages, failed requests), warnings[], validation
results (pypdf, page-count invariant, veraPDF summary if run), exit_code, and
the manifest checksum. Schema published in `schemas/qa-report.schema.json`.

`failed_requests` lists the first 20 distinct URLs that failed;
`counts.failed_requests` counts every distinct one, and the manifest's
warnings give that count by resource type, so the bundle says what was lost
without the report (docs/09 P20-6).

**Delivered in Phase 4** with these field names (docs/09 P4-5):
`schema_version: "2"`, the v2.2 keys unchanged, plus `timings` (seconds per
pipeline stage — a stage a run never entered is absent), `counts` (typed, and
`null` rather than `0` where a capture path does not produce a number),
`options` (resolved after flags/env/file), `validation`
(`pdf_readable`, `pdf_pages`, `page_count_invariant`, `pdfa`),
`manifest_sha256`, and `exit_code`.

Two deliberate changes from v2.2, both what the version bump is for:
`ai_summary` — whose shape depended on the capture path — is replaced by
`counts`; and `validation` is always present, with `pdfa: null` when the gate
did not run, so "was not checked" and "was checked and found nothing" stop
looking identical.

Credential-bearing options are **not** in `options`: `--storage-state` and
`--auth-profile` reduce to `auth_mode`. A QA report is a file people attach to
tickets, and the location of a session store is not something it needs to carry.

`exit_code` describes the run the report belongs to. **Phase 5.1 made that
true of failures too**: a run given `--report` writes one whether or not it
succeeded. A failure report publishes what the run genuinely knew — `source`,
`options` once they resolved, `duration_seconds`, `captured_at` — and null for
everything it never reached, on the same reasoning that chose null over 0 for
`counts`. It adds one field: `error`, the message, which is **omitted entirely
from a successful report** the way `timings` omits a stage a run never entered,
so `"error" in report` is a test rather than an interpretation. The
`--validate-pdf=strict` failure is the exception worth knowing about: it
publishes the file and *then* fails it, so its report is a complete record of a
capture plus the verdict that made it exit 7.

## 3. Exit codes (MUST)

| Code | Meaning |
|---|---|
| 0 | success |
| 2 | usage / config error |
| 3 | navigation or readiness failure (timeout, unreachable host, refused status) |
| 4 | authentication required (HTTP 401/403, or a redirect to a sign-in page, §5 item 4), or the auth material was rejected |
| 5 | capture integrity failure (e.g., protected page-count mismatch, or an empty capture under `--require-content`, §5 item 14) |
| 6 | OCR required but unavailable (only when OCR explicitly required) |
| 7 | PDF render/validation failure |
| 8 | bundle build/publish failure |
| 9 | environment failure (`doctor`-detectable) |

**Migration note (breaking, deliberate):** v2.2 exits 1 for every failure. v3
never emits 1 — scripts testing `$? -eq 1` must switch to `-ne 0`. Distinct
codes are the feature; aliasing 1 would defeat them. Called out in the
migration guide and CHANGELOG.

**Where the lines fall.** Two boundaries decided every reclassification in
Phase 5.1, and both are stated here rather than left to be inferred (docs/09
P5-1):

- **2 against 4.** 2 is "the arguments could not be used as given" — a file
  that is not there, a combination the model refuses, a value out of range;
  all of it decided before anything runs. 4 is "the far end refused, or the
  auth material was rejected" — a decision only the site can make.
- **3 against 5.** 3 is "the page never became available to capture". 5 is
  "the capture ran and what came out cannot be trusted", which is why the
  protected path's page-count and back-link invariants live there.

**Migration status — complete as of Phase 5.1.** The taxonomy was adopted a
path at a time: Phase 1 made **2**, **7** and **9** real; Phase 2 added **8**.
Phase 5.1 classified everything that was left, so **3**, **4**, **5** and
**6** are live and nothing returns 1 any more. `errors.EXIT_CODES` is the one
place the table is written down — `--help`'s epilogue and
`tests/test_exit_codes.py` both read it, and that test also asserts this table
lists exactly the same codes.

**Coverage.** Every code is exercised. 0, 2, 3, 4, 5, 6 and 9 are reached by a
real `webshot` invocation against a fixture or a loopback server: 4 by a 401
and by a redirect to a sign-in page on a second loopback origin, 5 by an empty
page under `--require-content` (docs/09 P22). 5's other trigger, 7 and 8 are
driven through the real code that classifies them with the fault injected: a
faithful trigger needs a SharePoint tenant that lies about its page count, a
PDF/A corrupted on purpose, or a disk that fills between the staging write and
the swap. 8 became one of them deliberately — §5.1's lock now touches every
published path before the capture starts, so a destination WebShot cannot
publish to is refused as a usage error up front rather than after a full
capture (docs/09 P5-10).

**Every failure writes its QA report.** A run given `--report` writes one
whether or not it succeeded, and the file carries the process's own
`exit_code` plus an `error` string saying why. Before Phase 5.1 a failing run
wrote nothing, which made `exit_code` 0 in every report that existed. The
fields a failure never reached are null rather than zero, and `error` is
**absent entirely** on a successful run, so its presence is the
machine-readable "this capture did not happen". Interrupting a run with
Ctrl-C exits **130**, the shell's own SIGINT convention and deliberately
outside this table.

## 4. Non-functional requirements

| Area | Requirement |
|---|---|
| Performance | Typical article (≤ 3 MB DOM, ≤ 30 assets): end-to-end ≤ 90 s on a dev laptop excluding OCR; OCR bounded by worker pool. No hard SLO — a smoke budget in CI catches order-of-magnitude regressions |
| Determinism | Two runs on identical input (fixture server) MUST produce equivalent bundles: byte-identical for text/JSON artifacts (timestamps/versions normalized); raster assets compared by dimensions + format + within-run SHA (browser/OCR upgrades legitimately change pixel bytes — policy in 06) |
| Offline | After install + `playwright install chromium`, local-file pipeline MUST run with no network (no runtime model downloads — gate on the chunker decision, ADR-0008/Phase 2) |
| Footprint | Base install (no extras) MUST NOT pull torch or any GPU stack |
| Portability | macOS + Linux CI-gated; Windows best-effort, measured weekly by `windows-smoke.yml`, whose failure fails its own run and blocks no pull request (docs/09 P10-24) |
| Python | ≥ 3.11 (raised in Phase 1: OCRmyPDF 17.x requires it — docs/09 P1-1) |

### 4.1 Known Windows limitations

"Best-effort" means the gaps below are known and stated, not that they are
absent. Each one was found by the weekly Windows run or by reading the code
it failed in (docs/09 P10-24). A test that cannot run on Windows is skipped
there with its reason, and the run's job summary lists every skip.

- **onnxruntime telemetry is switched off after its import, not before.** The
  `ORT_DISABLE_TELEMETRY=1` WebShot sets (docs/09 P10-14) is read only by
  non-Windows builds. The Windows build emits ETW TraceLogging events
  instead. By onnxruntime's `Privacy.md`, the operating system records them
  while a trace session is collecting and may send them to Microsoft based on
  the user's consent. WebShot calls `onnxruntime.disable_telemetry_events()`
  as onnxruntime's import finishes, before whatever imported it can build a
  session, and `ORT_DISABLE_TELEMETRY=0` opts back in as it does elsewhere
  (docs/09 P10-26). The weekly run's trace shows no session event under that
  default. Three gaps remain. First, onnxruntime writes three events while
  its native module loads, before any Python can run: `ProcessInfo` (runtime
  version, CPU model, processor count, memory, the process's service names)
  and the start and end of its internal execution-provider registration.
  Second, a trace session that enables the provider after the call switches
  the events back on for the rest of that process, because onnxruntime's ETW
  callback overwrites the flag the call cleared. Third, sessions a library
  caller built before importing WebShot have already been reported. The
  Windows module carries neither the 1DS uploader nor the exit-time abort
  P10-14 fixed.
- **The output lock's symlink guard is a check, not a flag.** Windows has no
  `O_NOFOLLOW`. The lock is created with `O_EXCL` only when nothing has its
  name, and otherwise refused unless the name is a plain file that is the one
  opened. A dangling link planted in the instant between that check and the
  create can still leave an empty file at its target before the run is
  refused. Planting a link needs the create-symbolic-link privilege, which by
  default only an administrator holds, or Developer Mode.
- **Not measured on Windows:** the golden corpus and parity baselines
  (recorded on macOS, because Chromium's text layout is platform-dependent —
  docs/09 P0-6), the protected-viewer path and everything else that needs
  Tesseract (the runner has none), and the tests that need the `mcp`,
  `office` or `ocr-rapid` extras, which the weekly run does not install.

## 5. Behavioral edge cases (normative)

1. **Concurrent runs, same output path:** a per-output lockfile makes the
   second run exit 2 with a clear message.
2. **Unicode/exotic sources:** slug = transliterated stem + 8-hex hash of the
   source URL/path — collisions impossible by construction (fixes v2, where
   non-Latin URLs all collapse to `web-page`).
   *Status: not implemented on the CLI path.* The CLI still names its default
   output with the v2.2 slug (`acquire/router.py`'s `sanitize_filename`), which
   the golden corpus pins, so two such sources can still share a name unless
   `--output` separates them. The MCP server appends a 12-hex digest of the
   full source to every name it publishes (docs/09 P8-54).
3. **Cross-artifact atomicity:** PDF and bundle are staged in one temp
   directory; the manifest (containing the final `pdf.sha256`) is written
   last; bundle dir is renamed first, PDF second. The crash window between
   the two renames is detectable — the manifest hash won't match — and
   consumers MUST verify checksums; documented in the bundle guide.
4. **Redirects:** followed by the browser; `final_url` recorded. A redirect
   landing on an authentication wall (password field present, different
   origin) exits 4 with guidance. `--allow-http-errors` governs 4xx/5xx
   capture, as in v2.
   Both signals, and only both: the page the browser ended on has a
   different scheme, host or port from the first URL of the navigation, and
   a password field in its document or an open shadow root is visible (laid
   out, not hidden by CSS, not parked off the page). A sign-in page asked for
   directly is captured, and so is a redirect to a page whose password fields
   are hidden. The check runs after every wait and after `--interactive-auth`'s
   prompt; under `--wait-for`, a wait that times out on such a page is exit 4
   rather than 3. A landed page that cannot be read, tried twice, is a
   manifest warning that the check did not run, never a pass. The guidance
   names `--storage-state` and `--auth-profile`,
   and says to give the landed page's address directly if it is the page
   meant. Detection only: nothing is submitted to the page (docs/11). Not
   detected: a sign-in page on the same origin, a password field inside a
   frame, and a sign-in flow that asks for the user name before it shows a
   password field (docs/09 P22-1).
5. **Overlay policy (was undocumented in v2):** `clean` mode hides
   cookie/consent overlays and records the hidden-element count in the
   manifest; `faithful` mode hides nothing. Capture is an act of record —
   what was altered must be declared.
6. **Frames and shadow roots:** the snapshot is the page's flat tree. Every
   open shadow root, and the document of every frame the page can script
   (same-origin, `srcdoc`, `about:blank`), is read where it renders, so its
   text is in `content.html` and everything extracted from it. A frame no
   page script can reach (cross-origin or sandboxed) is captured visually:
   its screenshot asset stands in `content.html` where the frame renders, as
   an `<img>`. The text OCR recognized there is attached to that picture as an
   image's is: a `webshot-ocr` annotation in `content.json`, and from there
   `content.md`, `content.txt` and a figure chunk. A closed shadow root cannot
   be read at all. Each is named in a manifest warning with what the bundle
   holds of it, and a check for closed roots that could not run is a warning
   too (docs/09 P16-6). A frame that was read is not warned about. Each
   iframe's asset records which path applied, in `frame_content`: `dom` when
   its document was read, `screenshot` when its screenshot is all the bundle
   holds of it. The field is null on every other asset (docs/09 P16-7).
   The visuals in every tree the snapshot reads are harvested like the top
   document's, numbered in reading order (docs/09 P16-9). Every heading a
   stage reports, a visual's `nearby_heading` and an embedded video's alike,
   is the last heading before the element in that order (P16-12). A
   screenshot scrolls what it photographs into view, and Chromium prints a
   frame and a scrolling box as they stand, so every scroll position the
   harvest moved, in every tree, is put back once its visuals are captured.
   One that cannot be put back is a warning (P16-9, P16-12). A frame prints
   its box, not its document. So before the bundle, each frame the page can
   script, and a reader can scroll, is grown on trial to its document's height
   at the printed width. What still falls outside a frame's box is a warning
   naming the frame. A frame whose author turned its scrolling off is not
   grown, and its warning says that neither the page nor the PDF shows the
   text it hides (P16-16).
   Extraction stops at the page-script line; print preparation may cross it,
   decided frame by frame (item 10, docs/09 P16-21). A frame from another origin may
   have its scrolling boxes released for print. That never happens to a frame
   holding a sign-in or payment field, it is kept only if every released box
   still fits inside the frame, it is logged with its reason, and it MUST be
   checked against the printed layout, with a manifest warning if it did not
   hold.
7. **OCR degradation:** warning by default; exit 6 only under `--require-ocr`.
8. **Validating a file that never claimed PDF/A:** `--validate-pdf` checks a
   file against the conformance level it declares in its own XMP. A web-path
   capture declares none (ADR-0010 rejects that conversion), so the gate
   reports that there was nothing to validate rather than reporting failure
   against a standard the file never invoked — and `strict` does not fail on
   it. An absent validator is likewise a warning at both levels: a gate that
   could not run has not found a problem. The CI job is where the check has
   teeth, because there the validator is always present.
9. **MCP operational rules:** `capture` emits progress notifications and
   documents that client timeouts must exceed the operation timeout; one
   capture at a time per server instance (queued); `read_asset` capped
   (default 5 MB, paged); allowlisted roots come from `webshot.toml`
   (`[mcp] roots = [...]`); profiles needing interactive first-run are
   refused with instructions to run `webshot --interactive-auth` in a
   terminal.

   Added in Phase 4 (docs/09 P4-4), as consequences of the same rule:

   - `read_markdown` is capped and paged the way `read_asset` is
     (`[mcp] markdown_page_bytes`, 256 KB by default), and both page by **byte**
     offset so a resumed read lands where the previous one stopped;
   - `query_chunks` caps `limit` server-side and reports `matched` alongside
     `returned`, so a truncated answer is distinguishable from a small bundle;
   - `output_root` (default `output/pdf`) is always a readable root, so a server
     configured with no `roots` can still read what it produced and nothing else;
   - an MCP capture is **staged** inside the output root and published only after
     every check has passed, which is what makes §6.8's redirect rule a refusal
     to publish rather than a deletion afterwards;
   - diagnostics go to stderr: on stdio, stdout is the protocol.
10. **Text past the printed page's edge:** Chromium clips whatever a print
    layout places past the page's left or right edge, in the render and the
    text layer alike, while the bundle keeps it. After the render, a capture
    MUST measure the page at the width Chromium printed it at (read back from
    Chromium, not derived from the paper: `@page` rules and shrink-to-fit both
    move it) and record a manifest warning naming how many elements have text
    cut and where. A measurement that could not be made is itself a warning.
    It is not an exit code: the PDF is the page less what the edge cut, and
    the warning says so. WebShot does not reflow a layout that runs off the
    page; `--css` is the remedy (docs/09 P16-1, P16-4). Text that an
    `overflow: auto` or `scroll` box shows only when scrolled is lost the same
    way. A box capped by its own height that can grow without covering
    anything is released instead (docs/09 P16-10), and so is a pane whose
    height a layout sets, together with the boxes above it and, in a grid,
    the grid's row sizes, where that covers nothing (P16-13, P16-14). A visible
    panel positioned over the page is first put into the flow where it sits
    in the document, and the capture log MUST say how many were moved
    (P16-15). The same releases apply inside every frame the page can script
    (P16-17), and, decided and checked frame by frame, inside frames from
    another origin (P16-21; see item 6). A table in a sideways scroller that fits the page once its
    cells may wrap is released too, measured at the width the page prints at
    (P16-11). Any other such box MUST be its own warning, naming the box, in
    the page and in every frame that prints, whatever its origin (P16-18).
    Text in such a frame that the page's edge cuts is counted in the
    page-edge warning, which then names the frame (P16-19).
    Text hidden from the screen as well, by an `overflow: hidden` box or in a
    box parked off the page, is not a loss and MUST NOT be counted (docs/09
    P16-8, P16-15).
11. **Ligatures:** every page WebShot prints (the capture, its running header
    and footer, and every appendix) is printed with common, discretionary and
    historical ligatures off. That includes elements whose own CSS turns them
    back on, in every frame and open shadow root. Otherwise Chromium's text
    layer records "fi" as U+FB01 and a search of the PDF misses the word
    (docs/09 P16-2, P16-5). Contextual alternates and the page's other font
    features are left as they are. After printing, the text layer MUST be
    checked: any ligature presentation form (U+FB00–U+FB06) still in it is a
    manifest warning that names the forms.
12. **The OCR text layer:** the text recognized in each visual asset is
    printed beside the asset as an invisible layer, so a search of the PDF
    finds it. The layer MUST NOT widen the printed page: it spans a box the
    page already lays out, so Chromium's shrink-to-fit never scales a page
    down to take it in. Its text MUST reach the PDF's text layer, including
    where the asset starts a page (docs/09 P16-20).
13. **Recognized text on the reading surfaces:** wherever the text recognized
    in a visual asset reaches `content.md`, `content.txt` or `chunks.jsonl`,
    it MUST stand between an opening line that names the asset, the engine
    and the mean confidence, and a closing line that names the asset:
    `[OCR text from asset-001, machine-recognized by Tesseract, mean
    confidence 81%:]` and `[End of OCR text from asset-001]`. An engine that
    reports no confidence is written as "no confidence reported". A reader
    can then tell recognized text from the page's own words (docs/11
    principle 7). `content.json` keeps the `webshot-ocr` annotation unmarked,
    beside the record that holds its confidence, and the v2.2 layout under
    `legacy/` keeps the form v2.2 wrote. No confidence threshold keeps
    recognized text off a reading surface (docs/09 P20-4).
14. **An empty capture:** a capture whose `content.txt` holds fewer than 50
    characters of text, not counting the newline the file ends with, whose
    page showed no visual (saved, past `--max-assets`, or failed to save) and
    which has no embedded video, MUST carry a manifest warning
    saying so and naming the remedies. `--require-content` makes it exit 5
    instead, refused before the render, so neither the PDF nor the bundle is
    published. The check reads the bundle, so it does not run under
    `--no-ai-bundle`, where nothing records a count either. The figure is the
    one Crawl4AI's structural check uses for minimal text; a one-sentence page
    clears it (docs/09 P14-1, P22-2).

## 6. Security requirements (carried forward and new; all MUST)

1. Storage-state files and auth profiles are never copied into bundles or PDFs.
2. Password/`type=password` input values are redacted from all semantic artifacts.
3. Auth profiles are **owner-only**: nothing in one is reachable by anyone but
   the user running WebShot and the principals that can read every file anyway
   (root; on Windows, SYSTEM and Administrators). On POSIX a profile is created
   `0700`, which also keeps everyone out of what is inside it. On Windows it is
   created with a protected DACL that grants only those three and is inherited by
   everything created inside, and **every entry** is checked, not the directory
   alone: Windows skips traverse checks for every user by default, so a private
   directory does not make its files private (docs/09 P10-25). The CLI makes an
   existing profile owner-only before using it — `chmod 700`, or on Windows the
   same DACL, which Windows pushes down to every entry that inherits — and refuses
   one it cannot make so, naming the entry and the fix; the MCP server refuses
   one that is not (§6.8e). There is no override.
4. Protected-viewer path captures only viewer-rendered pages; never fetches the native file; never inspects credentials. Documented as a product invariant.
5. Sanitized HTML via nh3 allowlist; no `<script>`, event handlers, or non-http(s)/data/mailto URL schemes survive.
6. All XML parsed through defusedxml.
7. Local inputs > 100 MB rejected.
8. MCP: no credential-bearing parameters; filesystem access confined to configured roots.
9. `pip-licenses` CI gate: installed tree contains no GPL/AGPL/SSPL packages.
10. **A settings file is loaded only from a path someone named** — `--config` or
    `WEBSHOT_CONFIG`. There MUST be no working-directory discovery of
    `webshot.toml` (added in Phase 4, docs/09 P4-3).

#### 6.8 in full (Phase 4, docs/09 P4-4)

The MCP server is the surface an attacker reaches *through a captured page*: the
page's text becomes an agent's context, and that agent calls these tools. Every
rule below is a MUST, is deny-by-default, and has a test that proves the refusal.

| # | Guardrail |
|---|---|
| 6.8a | Filesystem reads are confined to the configured roots, resolved **through symlinks**. Refused: an absolute path outside every root, `..` traversal, and a symlink inside a root pointing out of it. `~` is not expanded in caller-supplied paths. A `file:` URL that names a host other than `localhost` is refused, because the check reads the path and on Windows that URL is a UNC path on the named machine (docs/09 P10-24). Paths a *manifest* names are validated identically — a bundle's own records describe a captured page |
| 6.8b | Captures write only under `[mcp] output_root`. No MCP parameter selects an output path; the destination is computed from the server's root and the source's slug |
| 6.8c | MCP-initiated captures refuse private, loopback, and link-local targets by default — RFC1918, 127/8, 169.254/16, ::1, fd00::/8, and, because the rule is `not is_global` rather than an enumeration, CGNAT and the documentation, benchmarking and future-use blocks too (docs/09 P4-11). An IPv6 address that spells an IPv4 one — IPv4-mapped, IPv4-compatible, 6to4, NAT64's well-known prefix `64:ff9b::/96`, or IPv4-translated — is classified as that IPv4 address, and `fec0::/10`, `64:ff9b:1::/48` and Teredo `2001::/32` are refused whole (docs/09 P4-12, P10-30). A host is classified under **every** reading it has — the strict literal, the `inet_aton`/WHATWG legacy literal a browser applies, and the resolver's — and one internal reading refuses it, because the readings disagree in both directions (`0177.0.0.1` is loopback to a browser and public to `getaddrinfo`). Every address a name resolves to is checked; an unresolvable name is refused rather than attempted. Opt-in with `[mcp] allow_private_networks = true` for the legitimate localhost-dashboard case. After the capture, `final_url` is checked against the same policy, and a capture that redirected inward is **not published**. The rule also applies to what the *page* fetches: an MCP capture aborts every request to an internal address, because a guardrail that checks only the top-level URL is one an attacker walks around with an `iframe` or a `fetch()` (docs/09 P4-13). **The CLI is unchanged** — it is human-driven |
| 6.8d | No tool takes a credential-bearing parameter. Auth profiles are referenced by **name** only, resolved through `[mcp.auth_profiles]`; a path-, JSON-, or whitespace-shaped value is refused before the lookup, and an argument the tool never declared is rejected rather than silently dropped |
| 6.8e | A profile whose first run needs a person is refused with the `webshot --interactive-auth` command to run; so is a profile that is not owner-only by §6.3's test — the directory's mode bits on POSIX, every entry's owner and DACL on Windows — and the refusal names the entry and the fix (§6.3, enforced here) |
| 6.8f | One capture at a time per server instance, queued, with progress notifications |

A denial MUST say which rule refused and what a legitimate caller does instead:
a refusal an agent cannot act on becomes a retry loop.

**What 6.8c cannot cover, stated rather than implied:** the policy checks the
name WebShot resolves, and the browser resolves it again a moment later, so a
name whose answer changes in between (DNS rebinding) is outside this check. What
it does close is what an agent can be steered into — an internal URL, or a public
one that redirects inward.

## 7. Compatibility & versioning policy

- Bundle `bundle_format` integer bumps on breaking layout/schema change;
  manifest always self-describes. v2-consuming code can detect v3 by the field.
- JSON Schemas in `schemas/` are versioned with the package and published in
  releases; additive changes are minor, breaking are major.
- Public Python API: the real v2 surface — `webshot.convert_url_to_pdf()` and
  `webshot.main()` — keeps working; the two 7-line legacy entry-point shims
  are retired at v3.0 with a release-note pointer.
