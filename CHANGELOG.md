# Changelog

All notable changes to WebShot are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and WebShot follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html) with the additional
compatibility rules in [docs/04-spec.md §7](docs/04-spec.md): the bundle's
`bundle_format` integer bumps on a breaking layout or schema change, published
JSON Schemas are versioned with the package, and the manifest always
self-describes.

This public repository starts from a fresh copy of a private one. The entries
below carry over from it, so the version history is complete even though the
commit history begins here. References such as "docs/09 P10-25" name numbered
entries in the findings log, [docs/09-spike-report.md](docs/09-spike-report.md),
which records the measurements behind each change.

## [Unreleased]

### Added

- **`webshot journey <URL>` walks a training journey and captures every module
  in it.** A journey on Continu, a learning-management platform, is a
  single-page application: five levels of accordion — journey, section, track,
  group, module — behind **one URL**. Its modules have no addresses of their own, so a list of URLs to
  capture cannot be written, and clicking through the tree is not a convenience
  over that list but the only way in. This command is that walk. It is
  navigation and coverage; each module it opens is captured by the pipeline that
  already existed.

  **Enumerate, then capture what the enumeration listed.** The walk runs in two
  passes: it builds an outline of the whole tree, walks it a second time and
  diffs the two, and only then captures — exactly the module set the outline
  names, no more and no less. `--dry-run` stops after the enumeration and writes
  `outline.json`; it is not an optional preview but the contract the capture
  pass is checked against, which is why `--no-verify-determinism` says in as
  many words that skipping the second walk makes the outline unusable as one.

  **Coverage is proved, not assumed.** Every level asserts its own child count
  against what the application listed, per node and never in total — a
  journey-wide "247 modules" is satisfied by a group that silently returned
  nothing, which is precisely what a stale element reference produces. Errors
  are reported in both directions: fewer children than listed *and* more.
  Resume is keyed on a synthetic path id **and** a digest of the outline it was
  built against, so a run resumed against a journey whose shape has changed
  refuses rather than skipping work it never did.

  **The safety posture is the most important thing about this feature.** It is
  read-only against a journey that is already complete: progress is asserted
  stable before and after the walk, and it never presses "Next in Journey" or
  "Next in Track", because a walker that advances a real training record is not
  a capture tool. **Assessments are refused, recorded, and never opened** —
  opening one may create an attempt against that record. The refusal is an
  **allowlist**: only `article` is opened, because a module type this build does
  not recognise may be an assessment under another name, and a denylist of
  "anything but assessment" would open all three of the unrecognised types the
  fixture reproduces. `--allow-module-type` extends the list after a human has
  looked at one, and can never be given `assessment`.

  **The corpus is built for the agent that reads it later.** It lands five
  levels deep, one directory per node, each module keeping its own bundle so it
  can be handed to an agent alone. `index.md` describes the journey and ends
  with **"What this corpus does not hold"** — every module that was part of the
  journey and was not captured, with the reason; and when there are none it says
  so explicitly rather than leaving an empty section, because "nothing was
  refused" and "nobody checked" must not look the same. Every chunk carries its
  full five-level path, so a retrieved fragment knows where it belongs without a
  second lookup — two modules in different groups can share a heading, and a
  chunk without it is indistinguishable from the other one's. `media/` holds one
  copy of each distinct asset with the list of modules that used it, hashed from
  what capture already wrote rather than by reopening anything. Cross-references
  between modules resolve by title where a title is unambiguous and are recorded
  as **ambiguous** where it is not, listing every candidate — a reference that
  guessed past ambiguity would be a citation the corpus cannot support.

  **`--min-interval` paces the walk** (default 1.0 s between module opens),
  which is docs/11 §2's per-host requirement for batch mode. The achieved
  intervals are reported rather than the configured floor, because a floor
  beneath a cost that already exceeds it never fires, and measuring is the only
  way to know which of the two a run was in.

- **The videos embedded in a page are captured with it.** A page whose
  procedure lives inside a player used to capture as a dead rectangle: the PDF
  showed a frame and the bundle knew a video existed. Where that video is a
  [Guidde](https://www.guidde.com/) walkthrough, WebShot now reads the authored
  steps behind it and writes them out as text — the instruction per step, the
  narration on the video's own clock, and the annotated screenshot for each
  moment — so an agent handed the artifact can learn the procedure without
  playing anything.

  In the **bundle**, each video gets `videos/<playbookId>/` holding
  `walkthrough.md`, `steps.json`, `transcript.txt` and a timed
  `transcript.vtt`; its step stills land in `assets/` with typed records; and
  `content.md` inlines the walkthrough in the section the player appeared in,
  so reading order matches the page rather than relegating the video to an
  appendix. `chunks.jsonl` gains one chunk per step, each naming the playbook,
  the video's title, the step number, its start and end, and its screenshot —
  enough for an agent to say "this came from video X, step 3, at 0:42" and be
  right. `manifest.json` records every video found.

  In the **PDF**, a walkthrough appendix follows the captured pages with one
  bookmark per video and one per step. Those appended pages carry no structure
  tags — merging an appendix does not preserve one (docs/09 S8) — and the
  manifest says so rather than letting the file's `tagged` flag imply
  otherwise.

  The `README.txt` embedded in the PDF names each video and where to read it,
  which is what makes any of it discoverable to an agent holding only the PDF.

- **A page's own caption tracks are read, and become the transcript.** Where a
  `<video>` or `<audio>` ships a `kind="captions"` track, WebShot sets it to
  `hidden` so the browser fetches and parses it — through the page, so the
  request crosses the capture's network policy like any other subresource —
  and publishes the cues as the media's transcript: `transcribed: true`,
  `transcript_provenance: "authored"`, the words inlined into `content.md` and
  `content.txt` where the player appeared, chunked into `chunks.jsonl` as
  `media_transcript` with a start and end time, and written to
  `videos/media-NN/transcript.txt` and `.vtt`.

  **A `subtitles` track is a translation, and is never treated as one.** HTML
  makes `subtitles` the default for a `<track>` with no `kind`, so only an
  explicit `captions` is accepted — a German subtitle track is not a
  transcription of English speech. The translation is kept as
  `videos/media-NN/translation-<lang>.vtt` and named in the manifest, and is
  deliberately **not** inlined or chunked: those are the retrieval surfaces,
  and text that enters them is indistinguishable from source at the point it is
  retrieved.

- **Media WebShot cannot document is named, never omitted.** A plain `<video>`
  or `<audio>` with nothing readable behind it is recorded with its source URL,
  duration, poster and declared caption tracks, marked `transcribed: false`,
  and said plainly in `content.md` and in the embedded README — because spoken
  content an agent cannot reach is far worse unlabelled than labelled:
  unlabelled, the bundle reads as complete when it is not. The note says which
  of three things happened: no track at all, a track the browser could not read
  (every `file://` document is an opaque origin, so its caption file is fetched
  and discarded), or a track that is a translation rather than a record of what
  was said. `manifest.video_tally` counts each kind —
  `documented_walkthroughs`, `transcribed_media`, `untranscribed_media`, which
  are exhaustive and sum to `total` — so a journey-wide count is a sum of
  manifests.

- **`--no-videos`** skips the whole enrichment; the capture is then byte-for-byte
  what it was before this existed. **`--video-assets`** additionally downloads
  each step's clip and the source recording — off by default, and not because
  of size: an agent cannot watch a video, and unplayable media is bulk rather
  than content. Both are settable as `[capture] videos` / `video_assets`.

### Security

- **pyjwt 2.15.1 and urllib3 2.8.0**, up from 2.13.0 and 2.7.0, which clears
  16 published advisories the vulnerability audit reported against the lock.
- **Authentication profiles are owner-only on Windows too.** There the CLI's
  `chmod(0o700)` set no ACL, so a profile was as private as the folder above
  it, and the MCP server refused every profile because mode bits cannot
  describe an ACL. The CLI now creates a profile with an access list that
  admits only you, SYSTEM and Administrators, inherited by everything inside,
  and rewrites an existing profile's list when it admits anyone else. Windows
  lets any user open a file by path inside a folder they cannot open, so every
  entry in the profile is checked, not only the folder. What the rewrite cannot
  fix, the CLI refuses with exit 2, naming the file and the `icacls` command.
  The MCP server accepts an owner-only profile and refuses any other by name.
  See docs/09 P10-25.
- **`--block-private-requests` now covers the video asset downloads.** The flag
  is enforced with a Playwright route handler, and route handlers apply to
  page-initiated traffic only — the asset fetches went around it. The URLs come
  out of the video service's own response, so third-party data named the fetch
  target, and the bytes land in the bundle and inside the shared PDF. The check
  is gated on the flag exactly as the page gate is, because an LMS on an
  intranet serving media from a private address is a legitimate capture; and it
  re-checks the URL actually reached, before reading the body, so a redirect
  cannot walk past it. See docs/09 P7-6.
- **The vulnerability audit now covers every version the lock pins, on every
  platform.** pip-audit evaluates each requirement's environment marker against
  the machine it runs on and silently drops the lines that do not match, so the
  audit covered only the share of the lock its runner's platform installs and
  reported on all of it. Before transformers moved to a single 5.17.0, five of
  the lock's 177 pins matched no marker on the macOS runner the audit job then
  ran on, transformers 5.15.1 among them. A different five matched none on the
  Linux runner a fork's pull request was routed to, including the vulnerable,
  macOS-only transformers 5.8.1 (PYSEC-2026-3929).
  `tools/audit/check.py` strips the markers, audits every pin, and fails unless
  pip-audit's report accounts for each one. CI and `tools/verify.py` run it,
  and it queries OSV, which is the service the documentation always named;
  pip-audit's default is PyPI. See docs/09 P10-16.

### Changed

- **`webshot --help` lists the subcommands** (`doctor`, `schema`, `mcp`,
  `journey`), every option has help text, no option prints "(default: None)",
  and no help cites a design document. `webshot doctor` has an `optional`
  status for a tool that only an opt-in flag uses (Ghostscript for `--pdfa`,
  veraPDF for `--validate-pdf`), so a clean machine no longer reads as
  degraded; it still exits non-zero only on a failure.
- **Upstream log noise is quieted.** pypdf's trailer repairs, docling's
  hyperlink notes and fontTools' subsetting lines no longer print, and `-v`
  turns on WebShot's own debug output without trafilatura's and htmldate's.
- **The README and user guide were rewritten** around a quick start that
  works, with a new guide for signed-in and protected documents, setup steps
  for the MCP server in Claude Code and Claude Desktop, and troubleshooting for
  HTTP errors, TLS-inspecting proxies and an MCP server that will not start.
  A code of conduct and issue forms were added.
- **Recognized text is marked as OCR wherever it is read.** In `content.md`,
  `content.txt` and the chunks, each image's recognized text now sits between
  `[OCR text from asset-001, machine-recognized by Tesseract, mean confidence
  27%:]` and `[End of OCR text from asset-001]`, so a reader can tell the
  recognizer's reading from the page's own words. The reading itself and
  `content.json` are unchanged. See docs/09 P20-4.
- **Markdown tables are no longer padded.** Every cell was padded to the
  widest in its column, so one long cell widened every row; a test table's
  `content.md` was 84% spaces. Cells now have one space either side (9,872
  bytes to 1,546 on that table), and the table parses to the same cells. See
  docs/09 P20-7.
- **onnxruntime's telemetry events are off on Windows too.** Windows builds
  ignore the `ORT_DISABLE_TELEMETRY` WebShot sets, and write ETW events that
  the operating system may send to Microsoft: for each inference session, the
  model's file name, hashes and metadata, and its run timings. WebShot now
  calls `onnxruntime.disable_telemetry_events()` as onnxruntime's import
  finishes, whichever package imports it, and `ORT_DISABLE_TELEMETRY=0`
  opts back in as it does elsewhere. A trace of the weekly Windows run
  shows no session event under the default. The three events onnxruntime
  writes while it loads, before any Python runs, cannot be stopped this way.
  docs/04 §4.1 lists that and the other gaps (docs/09 P10-26).
- `report.json`'s `options` gains `videos` and `video_assets`. This is the only
  change to the output of a capture with no video in it — no PDF byte, bundle
  file, manifest or log line moved on any video-free fixture.
- **`webshot --version` and `webshot --help` start in about a twentieth of the
  time.** They loaded 1823 modules and nine native-extension libraries —
  Playwright, Pillow, pypdf, lxml, NumPy among them — to print a string, and
  took roughly a second doing it; they now load 158 and none, in about 0.06 s.
  Every invocation paid that cost, including the scripted `--version` a caller
  uses to check whether WebShot is installed. `python webshot.py --version`,
  the source-checkout entry point, went the same way: 1809 modules to 157.
  `webshot doctor` keeps its full set on purpose — its probes import the
  extraction stack in order to report on it — and its output is unchanged, as
  are `--version`'s and `--help`'s. See docs/09 P10-6.

  The cause was one import in `webshot/__init__.py`, not in the CLI: the
  package eagerly re-exported `convert_url_to_pdf`, so importing *any*
  submodule loaded the whole capture stack. The public API of
  [docs/04-spec.md §7](docs/04-spec.md) is unchanged — `from webshot import
  convert_url_to_pdf` still works, and now imports the pipeline at that moment
  rather than beforehand.

### Fixed

- **Tesseract no longer stalls when several pages are read at once.** WebShot
  recognizes pages and assets in parallel, and an OpenMP build of Tesseract
  (the Debian and Ubuntu packages) also starts a thread per core in each
  process. On a 4-core machine, three of four simultaneous runs on a page that
  takes half a second alone were still going after 200 seconds, and CI's
  Linux test jobs timed out on that page. Every Tesseract run now gets
  `OMP_THREAD_LIMIT=1`, as OCRmyPDF already does, unless the user set it.
  See docs/09 P20-10.
- **A dependency bump no longer reads as an output change.** The portable
  golden profile masked the Skia producer's version but not its counterparts
  on the OCR path, so an OCRmyPDF update failed the corpus on nothing but
  `OCRmyPDF 17.12.1` and `pikepdf 10.12.0` and the check called it a real
  change. Both versions are now masked; the tool names are still compared.
  See docs/09 P20-11.
- **No message tells a user to `pip install webshot`.** WebShot is not on
  PyPI, and `webshot` there is an unrelated project, so the office, RapidOCR,
  MCP and doctor hints that said `pip install 'webshot[...]'` would have
  installed it. They now give `uv sync --extra NAME` from the checkout, and a
  test checks every string in the package.
- **The office-format hint actually fires.** MarkItDown 0.1.8 wraps a missing
  dependency in `FileConversionException`, so the hint never matched and
  upstream's own `pip install 'markitdown[docx]'` was printed instead.
- **A missing local file exits 2 with "No such file".** `./typo.csv` became
  `https://./typo.csv` and failed as a network error (exit 3).
- **A replaced capture is announced.** Two inputs that share a default name
  (`report.csv`, then `report.json`) silently overwrote each other; the run now
  logs "Replacing existing ...".
- **A certificate error explains the usual cause.** `net::ERR_CERT_*` now adds
  that a TLS-inspecting proxy whose authority the browser does not trust is the
  common reason curl works where the browser fails.
- **WebShot's own placeholders no longer read as the page's text.** An
  unlabeled SVG, frame or canvas got the alt "Vector graphic", "Embedded frame"
  or "Rendered canvas visual", which reached the reading text as a caption,
  and a video or frame with no title of its own took its OCR description as
  one. They now keep the page's own label, or none. See docs/09 P20-1.
- **The wait for images no longer stalls on images that cannot load.** A lazy
  image with no box never starts loading, so the wait always ran out its 30
  seconds and then warned without naming anything. It now waits only for
  images that will print, switches the ones still lazy to eager loading, and
  names any image still loading. On a test page a capture went from 39.9 s to
  9.8 s. See docs/09 P20-2.
- **`--max-assets` counts visual assets, not candidates.** Hidden and tiny
  visuals took places under the cap and were then skipped, so a test page with
  three charts after 65 hidden pictures and icons captured none of the charts
  at the default cap. The warning now says how many visual assets were left
  out. See docs/09 P20-3.
- **Every failed request is counted.** The CLI printed the size of a 20-URL
  sample as the total. `report.json` and the MCP result now carry
  `counts.failed_requests`, and the manifest gets a warning with the count by
  kind (image, stylesheet, script and so on), so a bundle read on its own says
  a resource was missing. See docs/09 P20-6.
- **"1 page", not "1 pages",** in the line a capture ends on. See docs/09
  P20-5.
- **The loopback server the tests and the golden corpus capture from takes a
  browser's burst of connections.** It listened with a queue of 5, under the
  six connections Chromium opens to one host, and macOS resets a connection
  that does not fit while the serve loop is busy, so a request failed on a
  loaded Mac and nowhere else. See docs/09 P20-9.
- **A journey capture no longer stops, or empties a module's `chunks.jsonl`,
  on a chunk that holds U+0085, U+2028 or U+2029.** Chunk records carry these
  characters raw, and the journey's path stamp split the file with
  `str.splitlines()`, which breaks at them. The first half-record raised
  `JSONDecodeError` after the file had been opened for writing, so the module's
  `chunks.jsonl` was left empty and the journey stopped. The stamp now splits
  at `\n` only, and parses and stamps every record before it rewrites the
  file, so a line that is not a chunk record raises and leaves the file whole.
  The golden harness and the parity harness (which compares v3 output with
  recorded v2.2 output) read the file the same way and are fixed with it
  (docs/09 P10-28).
- **WebShot captures on Windows again, and the weekly Windows run says when
  it cannot.** Since the output-lock hardening of docs/09 P8-12, every Windows
  capture exited 5 at the output lock, which named `os.O_NOFOLLOW`. That flag is POSIX-only. A Windows `file:///C:/…`
  source was also read as the drive-relative `C:…` and reported as missing. The
  lock now keeps its symlink guard without the flag, and `file:` URLs are read
  with the platform's own conversion. The weekly `windows-smoke` run had hidden
  all of this for six weeks behind `continue-on-error`. It now fails when
  anything fails, runs the capture even after failing tests, and lists every
  failure and skip in its job summary. What Windows still does not do is
  docs/04 §4.1 (docs/09 P10-24).
- **A `file:` URL that names another host is refused.** The MCP source check,
  the subresource root check and the local-source reader looked only at the
  URL's path, so `file://server/<root>/page.html` passed as the file inside the
  root. On Windows that URL is a UNC path on `server` (spec §6.8a, docs/09
  P10-24).
- **A Windows capture writes the same bytes as a macOS or Linux one.** Bundle
  text artifacts were written in text mode with the platform's line ending, so
  on Windows every file (and its copy embedded in the PDF) carried `\r\n` and a
  different digest. Every writer now writes `\n`, and OCRmyPDF's
  `ocr/text-layer.txt`, which it writes in text mode, is converted to `\n` as
  well. `tables/*.csv` keep RFC 4180's `\r\n`, as they already did on every
  platform. macOS and Linux output is unchanged (docs/09 P10-27).
- **A `|` in a CSV or TSV cell no longer shows a backslash.** MarkItDown 0.1.8
  escapes the cell delimiter itself, so the bridge's own escape made a second
  one and the page read `a \| b`. The pipe is now left to MarkItDown; line
  breaks, HTML and Markdown openers are still escaped (docs/09 P10-23).
- **A nested table's caption is no longer reported lost when it was kept.** On
  docling-slim 2.130 the HTML backend keeps `<table><caption>` itself, but when
  a nested table made the table counts disagree the manifest still warned that
  the captions "could not be re-attached". The warning now counts only captions
  no table in the document carries (docs/09 P10-19).
- **An MCP refusal reaches the agent with its reason again, on mcp 2.1.** From
  2.1 the SDK shows the model only `Error executing tool <name>` for any
  exception that is not its own `ToolError`, so every policy refusal (a path
  outside the roots, a private target) lost the sentence saying which rule
  refused. The bridge now re-raises WebShot's own errors as `ToolError` and
  leaves anything unexpected masked (docs/09 P10-17).
- **A dependency that is missing or unloadable now exits 9 instead of
  crashing.** Because each stage is imported when it runs, an `ImportError`
  reaches the CLI's own error handling; spec §3 says v3 never exits 1, and this
  was a path that did, as a traceback. It is now an environment failure, which
  is the code that points at `webshot doctor`.

- **A walkthrough now reaches `content.txt`, not only `content.md`.**
  `inline_into_content` had exactly one call site and it was on `content.md`,
  so the procedure — the largest body of text this tool produces — was absent
  from the plain-text surface entirely: measured live, 78 of 78 modules
  carrying a walkthrough had a `content.txt` under 60% of their `content.md`,
  the worst 379 bytes against 10388. The argument against that was already
  written down beside it, for the media note: an agent that searches
  `content.txt` and finds no mention would reasonably conclude the page had
  none. The walkthrough is now rendered a second time with no markdown marks
  and appended, the way the media note already was.

- **Guidde's narration correction markup is resolved instead of published.**
  A narration script can carry `{written||||alt=spoken}`, a pronunciation hint
  for the speech synthesizer, and `{written||||spelling=Correct}`, the author
  fixing a written word — and the two publish **opposite** halves of the same
  shape. Both were reaching `content.md` and `chunks.jsonl` verbatim (95 and 48
  occurrences across 11 of 129 modules in one live journey). They are resolved
  at the bridge, so every surface rendered from those dataclasses is fixed at
  once, and a directive that is neither becomes a manifest warning naming it
  rather than a guess.

- **Text that is not a title is no longer recorded as one.** A `<video>` with
  no label of its own hands the DOM scan whatever the visual harvest wrote into
  its `aria-label`, which is the text OCR read inside the frame — one live
  capture titled a module's video with the recognized player chrome, newlines
  included, breaking a `content.md` heading across six lines and propagating to
  the corpus `index.json`. Recognized text and multi-line values are refused,
  the media element is recorded without a title rather than under a corrupt
  one, and the refusal is a manifest warning so absence does not read as an
  element the author never named.

## [3.0.0] — 2026-08-21

WebShot v3 is a rebuild around adopted open-source components rather than
bespoke ones. No custom code implements OCR layering, semantic extraction,
chunk splitting, format parsing or HTML sanitization any more; what remains is
capture, composition, and the guarantees around them. The user-visible result
is a bundle built on an ecosystem document model, a PDF that carries that
bundle inside it, five new input formats, an agent-facing MCP server, and
failures that finally say what went wrong.

### ⚠ Breaking

- **`bundle_format: 3`.** The web-path `content.json` is now a lossless
  [DoclingDocument](https://github.com/docling-project/docling-core) instead of
  WebShot's bespoke block format, and `chunks.jsonl` anchors into it by
  `self_ref` rather than by CSS path. Read
  [the migration guide](docs/migration-v2-to-v3.md) before upgrading a
  consumer — it maps every file, every renamed field, and where each v2 block
  type went. Detect the format from `manifest.json`, which self-describes.
  **The escape hatch:** `--legacy-bundle` additionally emits the exact v2.2
  `content.json` and `chunks.jsonl` under `legacy/` in the bundle. It ships in
  v3.0 and is **removed in v3.1** — it is time to migrate, not a place to live.
  The **protected-viewer bundle is unchanged** (`schema_version: 1.1`), and so
  is every PDF deliverable.
- **Python ≥ 3.11.** Raised from 3.9. OCRmyPDF 17.x, which builds the protected
  path's searchable text layer, requires it.
- **Exit codes 1 → 2–9.** v2.2 exited 1 for every failure. v3 never emits 1:
  each failure returns its own code from
  [spec §3](docs/04-spec.md) — 2 usage, 3 navigation, 4 authentication, 5
  capture integrity, 6 OCR required but unavailable, 7 PDF, 8 bundle, 9
  environment. Distinct codes are the feature; aliasing 1 would defeat them.
  **Any script testing `$? -eq 1` must switch to `-ne 0`.**
- **QA report schema v2.** `ai_summary` — whose keys differed between capture
  paths, so it could never be typed — is replaced by `counts`, in which a
  number a path does not produce is `null` rather than `0`. `validation` is now
  always present, with `pdfa: null` when the gate did not run, so "was not
  checked" and "was checked and found nothing" stop looking identical. The
  report is also written as UTF-8 without `\uXXXX` escapes, like every other
  WebShot artifact.
- **Licensed Apache-2.0.** Chosen for the explicit patent grant and alignment
  with the adopted stack.
- **The two legacy entry-point shims are retired**: `webshot_new.py` and
  `simple_web_to_pdf.py` are deleted. Use the `webshot` console script, or
  `python webshot.py` from a source checkout, which still works. The public
  Python API — `webshot.convert_url_to_pdf()` and `webshot.main()` — is
  unchanged and covered by the compatibility policy in
  [spec §7](docs/04-spec.md).

### Added

- **The web-path PDF now carries the bundle inside it.** A capture used to
  produce a PDF and a directory that had to travel together; it now produces
  one file that carries its own structured data as PDF associated files —
  `README.txt` first (it is what tells a reader the rest is there),
  then `content.json`, `content.md`, `content.txt`, `chunks.jsonl`,
  `assets.json`, `links.json`, `structured-data.json`, `accessibility.yaml`,
  `content.doctags`, every table CSV, and `capture.json`. Hand an agent the
  PDF and it has everything. `--embed-assets` adds the binary visuals;
  `--no-embed-bundle` restores the previous behaviour. The sidecar bundle is
  unchanged, and `manifest.json` gains `pdf.embedded_files` recording what
  went in. This is what docs/02 §PDF deliverable always specified for the web
  path and what only the protected path had ever done.

- **MCP server (`webshot mcp`, `webshot[mcp]`).** Seven tools — `capture`,
  `read_markdown`, `query_chunks`, `get_manifest`, `list_assets`, `read_asset`,
  `doctor` — so an agent can capture a page and consume the result without
  touching the filesystem. The surface is deliberately stricter than the CLI:
  reads confined to configured roots resolved through symlinks, captures
  published only under the server's own output root, private/loopback/link-local
  targets refused by default (including what the *page* fetches), no
  credential-bearing parameter on any tool, and one capture at a time. Read
  [the MCP guide](docs/guide/mcp.md) before integrating one — **returned page
  content is untrusted data, never instructions**.
- **Office inputs via `webshot[office]`:** DOCX, PPTX and XLSX. EPUB and ZIP
  need no extra.
- **`--ocr-engine`,** selecting Tesseract (default) or RapidOCR
  (`webshot[ocr-rapid]`), which is pip-only, ships its models in the wheel, and
  needs no system binary and no download. Every per-asset record names the
  engine that produced it.
- **`--config` and `WEBSHOT_*`.** Settings from a TOML file and the
  environment, resolved as flags > env > file > defaults. The file is read
  **only** from `--config` or `WEBSHOT_CONFIG` — there is no
  working-directory discovery of `webshot.toml`, because a directory of someone
  else's material must not get to choose WebShot's settings.
- **Tagged PDFs and heading bookmarks are now verified**, not merely requested:
  the manifest publishes what the file actually carries, and the golden corpus
  asserts it in both directions.
- **PDF/A-3 on the protected path (`--pdfa`).** A-3 rather than A-2b because it
  is the level that permits the embedded associated files the bundle rides in.
  Web captures are refused: the conversion strips the accessibility tags
  Chromium builds ([ADR-0010](docs/adr/0010-validation-gates.md)).
- **`--validate-pdf`,** optionally `=strict`. Checks the *published composed*
  file with veraPDF, from a local binary or the `verapdf/cli` container.
  `strict` turns a file that was checked and failed into exit 7; an absent
  validator stays a warning, because a gate that could not run has not found a
  problem.
- **`--require-ocr`.** Missing recognition becomes exit 6 instead of a manifest
  warning. Checked before the browser opens, against the engine that will
  actually run. Also `[ocr] require` / `WEBSHOT_OCR_REQUIRE`.
- **`webshot doctor`.** Per-item PASS/WARN/FAIL with a one-line fix for
  Chromium, Tesseract and its language packs, Ghostscript (and its version — 10.06
  has JPEG-corrupting bugs), veraPDF or a running Docker daemon, the extraction
  stack, the local-format converters, and output-directory permissions.
  `--json` for the machine-readable form.
- **`webshot schema [NAME] [--write DIR]`.** Prints or regenerates any
  published contract, so a consumer can validate against the schema that
  shipped with its WebShot rather than one copied out of a repository.
- **`content.doctags`,** docling's DocTags serialization, and **`assets.json`**,
  which carries typed visual-asset records (OCR text, confidence, language,
  checksums) alongside form-field and embedded-media records.
- **`manifest.json` records `tool_versions`** — the exact Playwright, Chromium,
  docling, chonkie, MarkItDown, Trafilatura, nh3 and Tesseract versions that
  produced the bundle — plus `auth_mode` and `page_metadata`.
- **A QA report for failures.** `--report` now writes a file whether or not the
  run succeeded, carrying the process's own `exit_code` and an `error` string.
  The `error` key is absent entirely from a successful report.
- **SECURITY.md,** with the disclosure process and the full security posture.

### Changed

- **Extraction is docling-based.** `content.md`, `content.txt` and the chunk
  splitter are docling's serializers and `HierarchicalChunker` (plus
  [chonkie](https://github.com/chonkie-inc/chonkie) for size-splitting, offline,
  with its character tokenizer) instead of hand-written ones.
  `docling-core[chunking]`'s tokenizer-aware `HybridChunker` is available behind
  `webshot[chunk-hybrid]` and is not the default — it downloads a HuggingFace
  tokenizer, and WebShot's local-file pipeline runs with no network
  ([ADR-0008](docs/adr/0008-ocr-engine-abstraction.md)).
- **Bundle contents.** `content.html` is now the nh3-sanitized standalone
  snapshot of the *visible* page with asset references rewritten to `assets/`;
  `tables/*.csv` come from docling `TableItem` grids with spans resolved; the v2
  YAML frontmatter on `content.md`/`content.txt` is gone, because that metadata
  belongs in — and is in — `manifest.json`.
- **All local formats are read by
  [MarkItDown](https://github.com/microsoft/markitdown)** behind a single
  bridge, replacing WebShot's bespoke parsers. Markdown itself skips the step,
  so relative Markdown links and images stay resolved against the source
  directory.
- **`--auto-selector` is backed by
  [Trafilatura](https://trafilatura.readthedocs.io/)** instead of a static
  `article`/`main` selector list, which remains the fallback. The manifest
  records which strategy chose the region, and publishes what the page declared
  about itself alongside what Trafilatura read from it.
- **The protected path's searchable PDF is built by img2pdf and OCRmyPDF**
  instead of a hand-built reportlab sandwich.
- The full `docling` metapackage is explicitly **not** a dependency; only
  `docling-slim` plus named extras, so the default install stays model-free.

### Deprecated

- **`--legacy-bundle`.** Ships in v3.0 so a v2 consumer has a release to
  migrate in; **removed in v3.1**, together with the frozen v2 models and the
  parity harness. Runs that use it emit v2-format artifacts under `legacy/` in
  the bundle, listed in the manifest with `legacy: true`.

### Removed

- **reportlab** — replaced by img2pdf + OCRmyPDF on the protected path.
- **bleach** — deprecated by its maintainers; replaced by
  [nh3](https://github.com/messense/nh3), which now carries every sanitization
  allowlist including the local-document one. CI asserts neither is importable,
  because a replacement is only real once the original cannot be reached.
- The two legacy entry-point shims, `webshot_new.py` and
  `simple_web_to_pdf.py` (see Breaking).

### Fixed

These are defects a v2.2 user could hit. Problems found and fixed in v3-only
code during its own development are not listed here — they never shipped.

- **Correction: non-Latin URLs can still collide.** An earlier version of
  these notes listed a fix here that 3.0.0 does not contain. The CLI still
  names its default output with the v2.2 slug, built from the host and the
  last two path segments with every character outside `A-Z a-z 0-9 . _ -`
  replaced, so two URLs whose paths have no ASCII-safe characters, or that
  differ only in their query string, still publish to the same name. Pass
  `--output` to keep them apart. The hashed slug remains the specified
  behaviour (docs/04-spec.md §5.2). MCP captures are not affected after a
  later fix: the server appends a digest of the full source to every name
  (docs/09 P8-54).
- **Two runs aimed at the same `--output` no longer interleave.** They did not
  collide over their temporary files, which carry the process id, but they
  published into the same PDF path and the same `.ai` directory — so the winner
  could be the PDF from one run and the bundle from the other, producing a
  manifest whose `pdf.sha256` did not match the file beside it. A per-output
  lockfile now refuses the second run with exit 2, names the process holding it,
  and says what to delete if that process is gone. A lock whose owner is no
  longer running is taken over, so a crash cannot make an output path
  permanently uncapturable.
- **The manifest records the OCR settings that were applied**, not the ones
  requested. Under `--ocr-engine rapid` — which has no page-segmentation model
  — `--ocr-psm` is warned and ignored, and the record says so rather than
  claiming a setting that never reached the recognizer.
- **A failing run is no longer silent about why.** v2.2 exited 1 and wrote no
  QA report at all; v3 exits with the code for what went wrong and writes the
  report either way.

### Security

The first item shipped in v2.2 and affects anyone holding a v2 bundle. The rest
hardened code that is new in v3 — found by review and red-teaming before
release rather than in the field — and they are listed because the guarantees
they establish are ones a reader of this release should be able to check.

- **A password value could reach `accessibility.yaml` in v2.2.** Extraction
  redacted as it read, but Playwright's accessibility snapshot reads the live
  DOM independently and reports textbox values, so one artifact leaked what
  every other one redacted. The snapshot is now scrubbed against the live values
  — raw and in their escaped-quoted spelling — and the corpus test asserts both
  that the value is absent from every published file and that the redacted
  record is present, because an absence assertion passes vacuously if extraction
  stops running.
- **MCP captures could be steered at an internal address through the page**
  (v3-only code, fixed before release). The network guardrail checked the top-level URL only, so a captured page could
  `<iframe>` or `fetch()` `http://169.254.169.254/` and the response landed in
  the bundle the agent then read back. Requests are now filtered at the request
  level, covering the document, its subresources and every navigation it makes;
  `final_url` is re-checked after the capture and a run that redirected inward
  is not published. Found by a red-team pass and demonstrated end-to-end against
  a live loopback server before it was fixed.
- **The internal-address classifier is `not is_global`,** not an enumeration
  (v3-only code, fixed before release).
  The obvious spelling missed RFC 6598 carrier-grade NAT and the documentation,
  benchmarking and future-use blocks. A host is classified under *every* reading
  it has — strict literal, the legacy literal a browser applies, and the
  resolver's — because those readings disagree in both directions, and an
  unresolvable name is refused rather than attempted.
- **MCP tools reject an argument they never declared** instead of silently
  dropping it, which is what the SDK does by default (v3-only code, fixed
  before release). Auth profiles are
  referenced by name only; a path-, JSON- or whitespace-shaped value is refused
  before the lookup.
- **Caller-supplied read caps are bounded** (v3-only code, fixed before
  release). `limit=-1` defeated the MCP read cap entirely, because `read(-1)`
  returns the whole file, and `limit=0` produced a page that never advanced.
- **All XML — including SVG — is parsed through `defusedxml`** as WebShot's own
  guarantee rather than an inherited one, and archives are checked before they
  are opened: expanded size and member count against the 100 MB limit, no member
  path escaping the extraction root, no symlink members.
- **A settings file is read only from a path someone named.** There is no
  working-directory discovery of `webshot.toml`.
- **A CI license gate denies GPL/AGPL/SSPL in the installed tree**, `pip-audit`
  scans the locked set on every pull request, and every GitHub Actions step is
  pinned to a commit SHA.

### Not published to PyPI

**v3.0.0 is released on GitHub only.** The distribution name `webshot` collides
with the established `webshot` and `webshot2` R packages on CRAN, and
`shot-scraper` occupies the adjacent Python niche; publishing under this name
before deciding how to resolve that would make the collision permanent. The
release workflow builds an sdist and a wheel, verifies them by installing the
wheel into an empty environment and using it, and attaches them to a GitHub
release. The Trusted-Publishing step is written and disabled in
`.github/workflows/release.yml`, with the four steps that would enable it — the
rename included — in a comment above it. Anything on PyPI under this name is
not this project.

v3.0.0 was released from the private repository this one was copied from, so
its tag and release assets are not here, and versions before 3.0 were never
published. Install from a source checkout (see the README).

## [2.2.0] — 2026-06

The last v2 release, and the baseline every v3 comparison is measured against.
Its bundle format (`schema_version: "1.0"`) is documented in
[the migration guide](docs/migration-v2-to-v3.md) and validated by the frozen
`schemas/legacy-*.schema.json` contracts, which ship until `--legacy-bundle` is
removed at v3.1.

[Unreleased]: https://github.com/kitterman-t/webshot-ai/commits/master
