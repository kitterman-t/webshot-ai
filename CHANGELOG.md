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

- **`--local-paths relative` keeps the capture machine's folders out of a
  local capture's bundle.** A page rendered from a local file recorded its own
  absolute `file:` URL, and every image and link under it, in `capture.json`,
  `README.txt`, `assets.json` and `links.json`, so each deliverable carried the
  capturing user's name and folder layout. With the option those URLs are
  written relative to the page's directory (`./page.html`, `./images/a.png`)
  as the bundle is written, so the manifest's digests still describe its
  files. A URL outside that directory is kept and counted in a warning, and a
  last pass names any bundle file that still spells the directory. The default
  is unchanged. See docs/09 P14-61.

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

- **A document's text can no longer fetch a URL, or restructure the page.**
  MarkItDown writes what a slide, a Word paragraph, a spreadsheet cell, an EPUB
  chapter or a text file in a zip says into Markdown as if it were Markdown, so
  `<img src="https://…">` typed in any of them became an image the capture
  fetched, and `# Q3`, `3.`, `*x*`, `===` or `: x` became headings, lists,
  emphasis and definitions. An image's alt text, a link's target and a code
  block could each close their own syntax and add an image. Every string a
  document contributes is now written as character references, which render as
  the character itself, so a page whose text needed none renders exactly as
  before. A zip's plain-text members are code blocks, as a top-level `.txt` is,
  and a CSV inside a zip is escaped as a top-level one is. The spreadsheet fix
  of P8-9 is corrected too: `=SUM(A1)` printed as `\=SUM(A1)`, and a trailing
  `{x}` was dropped. See docs/09 P28.
- **A code block in a list item can no longer fetch a URL.** A blank line in
  a `<pre>` inside a list item, or a line opening a heading or a quotation,
  ended the paragraph its fence was read in, and the lines after it were read
  as Markdown with raw HTML: an
  `<img src="https://…">` there became an image the capture fetched. That
  code is now a code block, whose text is text. Code in a quotation is
  docs/09 P47's, and code in a link or emphasis docs/09 P59's. See docs/09
  P44.
- **Code written into a line of text can no longer fetch a URL.** A `<pre>`
  inside a link, emphasis, struck text, a subscript or superscript, a
  quotation mark or inline code, in a Word file (through its style map), an
  EPUB or an HTML page, on its own or in a zip, had its fence written
  mid-line, where Python-Markdown does not read one. A blank line in the
  code, or a line opening a heading, a rule, a quotation, a link reference, a
  definition or a `<div>`, let the lines after it be read as Markdown with raw
  HTML, so an `<img src="https://…">` there became an image the capture
  fetched. Inline code whose own text ran over lines did the same with no
  `<pre>` at all. Such a `<pre>` is now inline code whose text is text, its
  lines kept apart on the page where they ran together, and inline code is
  written on one line, which the browser showed it as. See docs/09 P59.
- **Code in a quotation or a definition can no longer fetch a URL.** A blank
  line in a `<pre>` inside a `<blockquote>` or a `<dd>`, or a line opening a
  heading or a quotation, ended the paragraph its fence was read in, and the
  lines after it were read as Markdown with raw HTML: an
  `<img src="https://…">` there became an image the capture fetched. That
  code is now a code block, whose text is text. See docs/09 P47.
- **A release waits out its first days, and stalled updates raise an alarm.**
  Dependabot now waits 3, 7 or 14 days before it proposes a Python package's
  patch, minor or major release, and 7 days for a GitHub Action, so a
  hijacked or withdrawn release is not merged on its first day. Security
  updates are not delayed. A daily dependency watch fails, and keeps an issue
  open, when a direct dependency or a pinned action is two weeks past that
  wait, when a pinned action's tag no longer names its commit, or when a
  Dependabot pull request failed, never ran CI, lost its auto-merge or waited
  three days. An input it cannot read fails it too. See docs/09 P84.
- **A Claude review finding can hold the merge.** The review workflow now
  reports `claude review verdict` on every head of a pull request that is not
  a draft. It fails while a thread Claude started is unresolved, while a
  summary it posted reports something and has not been hidden as resolved or
  outdated, and while no review has finished on the pull request. Required,
  it keeps auto-merge from merging a head the review flagged before anyone
  answered.
  The review itself still runs once per pull request, and on a push only when
  none has run yet; the verdict calls no model, and it is re-run after a
  reply once the thread is resolved. Bots', forks' and the sync's own pull
  requests pass it without a review. See docs/09 P89.

- **NAT64, IPv4-translated and site-local IPv6 addresses are internal.** The
  private-network check classified `64:ff9b::a9fe:a9fe`, the NAT64 spelling of
  the cloud metadata endpoint `169.254.169.254`, as public, along with
  `64:ff9b::7f00:1`, `::ffff:0:7f00:1` and `fec0::1`. It now classifies an
  IPv6 address in either translation form as the IPv4 address it carries, so a
  public site reached through NAT64 stays reachable, and refuses deprecated
  site-local `fec0::/10` whole. Local-use NAT64 and Teredo were already
  refused and are now named, so that no longer depends on Python's address
  registry. This covers MCP captures, `--block-private-requests` and the video
  asset downloads. See docs/09 P10-30.
- **docling-slim 2.132.0**, up from 2.130.0, which clears seven published
  advisories the vulnerability audit reported against the lock. `content.json`
  now keeps a source em dash in an item's `orig`; the text surfaces are
  unchanged. See docs/09 P10-31.
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

- **A picture printed far finer than paper can show is reduced.** Chromium
  embeds a picture at its natural size whatever size it prints at, and a
  think tank's 4-page topic page was 86 MB: its card thumbnails were the
  photographers' originals, up to 6720×4480 pixels, printed at up to 2,300
  pixels per inch. After the render, each picture drawn at more than 600 ppi
  (at the largest size it is drawn) is reduced to 300 ppi at that size, in the
  codec Chromium chose for it, with its soft mask. A manifest warning names
  each one with its original and reduced size, and a picture the pass cannot
  rewrite exactly is named instead. The topic page is now 8.3 MB. A file with
  nothing to reduce keeps its bytes. See docs/09 P80.

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

- **An HTML file reads the same whatever line endings it was saved with.**
  The reader for an EPUB's chapters and a zip's HTML members kept each `\r`,
  where a browser reads `\r\n` and a lone `\r` as `\n`. So a copy saved with
  Windows line endings kept a `<pre>`'s blank first and last lines, and a
  `\r` before a `\r\n` was a line break in a list item's code but not at the
  top level. Line endings are now read as a browser reads them. See docs/09
  P92.
- **A link's target in content.md is the one the page wrote.** docling held
  a target that is not an absolute URL as a file path, and wrote the path:
  `docs/` lost its slash and `./notes.txt` its `./`, two adjacent links to
  `docs/` and `docs` became one, and on Windows `//host/x` was written
  `//host/x/`. Each target is now written as the page wrote it. See docs/09
  P92.
- **`--local-paths relative` finds the source folder written with `/` on
  Windows.** Its last pass looked for the folder only as Windows writes it,
  with `\`, so a bundle file that wrote it with `/` was not named. See
  docs/09 P92.
- **Recognized text is read as the UTF-8 Tesseract writes, whatever the
  locale.** WebShot decoded Tesseract's output with the locale's encoding. On
  Windows that is the ANSI code page, cp1252 on an English install, so a
  recognized "Café" was recorded as "CafÃ©" with no error. A recognized "Í"
  lost the whole output, and the bridge raised an `AttributeError` that no
  per-image handler catches. The output is now read as UTF-8. Bytes that are
  not UTF-8 still fail that image and are never replaced. Tesseract's
  messages and its language list, in `webshot doctor` too, show such a byte
  as an escape instead of failing. Where Python already read UTF-8, as under
  a UTF-8 locale on macOS and Linux, recognized text is unchanged. See
  docs/09 P91.
- **An image the OCR engine cannot read is one warning, not the end of the
  capture.** When onnxruntime failed while RapidOCR was recognizing an image,
  RapidOCR raised its own `ONNXRuntimeError`. The per-image handler did not
  catch that error, because it is neither a `RuntimeError` nor an `OSError`.
  So under `--ocr-engine rapid` the capture stopped with exit 5 and no
  bundle, and the error's message was a full traceback. Each OCR bridge now
  turns its engine's failure into WebShot's own `OcrRecognitionError`. The
  manifest's `warnings` then names the asset and the error on one line, the
  other images are still read, and `--require-ocr` makes it exit 6. The
  Tesseract path had one such gap: its UTF-8 output could not be decoded
  under a locale whose encoding is ASCII. The footprint test also drops
  `opencv-python`, which RapidOCR 3.10 no longer installs, and now fails on
  any name an extra does not install. See docs/09 P90.
- **On RapidOCR 3.10, a model that will not load is found before the first
  image again.** RapidOCR 3.10 builds its models on first use instead of when
  it starts, so `--ocr-engine rapid`'s check that the engine can run reported
  it ready without loading anything. A model onnxruntime could not load then
  ended the capture at the first image, where on 3.9 it was one manifest
  warning, or under `--require-ocr` the early exit 6. The bridge now loads all
  three models when it starts, as RapidOCR itself did up to 3.9. See docs/09
  P10-33.
- **`discovered_metadata.date` is a date the page declares, or nothing.** It
  was Trafilatura's, and htmldate found it even with its extensive search off.
  It read a date from the URL's path ahead of the page's own `<meta>`, from a
  heading ahead of a `<time>`, from a copyright year made into January 1st,
  and from a comment nested in the article's JSON-LD. A declared date later
  than today was dropped. htmldate 1.11 added the first `YYYY-MM-DD` anywhere
  in the text, which put "2026-07-16" in the `text-notes` golden. WebShot now
  reads the date itself from date `<meta>` tags, schema.org dates in JSON-LD
  and microdata, and `<time>` elements. It takes a publication over a
  modification and records nothing for a value that would need a guess, such
  as `05/03/2024` or a year alone. Title, author and sitename are still
  Trafilatura's. See docs/09 P82.
- **`discovered_metadata.author` and `.sitename` are what the page declares,
  or nothing.** They were Trafilatura's. A page that declared no author got
  one from the text of an element classed as a byline, which on an index
  page was the first listed post's author and on a repository page a
  commit's; a page that declared no site name got a part of its `<title>`, a
  Twitter account's name, or the URL's host. The names it did read were
  rewritten: `Doe, Jane` became two authors, and `R. Chen` became `R Chen`.
  WebShot now reads the author from JSON-LD and microdata `author`,
  `<meta name="author">`, `citation_author`, `article:author` and
  `DC.creator`, as written and joined with `; `, and the site name from
  `og:site_name`, a JSON-LD `WebSite` or `publisher`, and `application-name`.
  On 48 public pages, 10 authors and 11 site names that the pages did not
  declare in these forms are gone; 8 recorded values are now the declared
  ones, as written; 4 declared authors Trafilatura dropped are now recorded;
  and 7 authors and 34 site names are unchanged. Only the title is still
  Trafilatura's. See docs/09 P88.
- **A bot check's page served with HTTP 200 fails the capture.** A site behind
  DataDome answered 200, the check drew its block page over the article, and
  the capture printed "We detected unusual activity…" under the article's
  title and exited 0. The page is now read after the lazy-load pass for a
  signature that names the vendor: DataDome's frame from
  `captcha-delivery.com`, HUMAN's `#px-captcha`, or Akamai's "Access Denied"
  page with its reference. Each exits 4 with the check named and the advice to
  save the page from your own browser, as a 403 from a bot check already did.
  A phrase a page could quote is never matched. And a navigation that fails
  with `net::ERR_HTTP2_PROTOCOL_ERROR`, which a news page did and then opened
  in Chrome, now says that some bot checks refuse an automated browser that
  way; it is still exit 3. See docs/09 P78.
- **A list keeps the number it starts at.** A Markdown list that began `3.`,
  and an EPUB list that began at 5, were numbered from 1 on the page, because
  the local-document allowlist dropped `ol[start]`. The snapshot kept the
  attribute with whatever text the page wrote, so `start="²"` failed the
  capture with exit 8 when docling read it as a number, and `start="٣"` was
  recorded as 3 where the browser showed 1. Both allowlists now keep `start`
  as the integer Chromium reads from it, and drop it where Chromium reads none.
  Word lists are still numbered from 1: mammoth does not read where one
  starts. See docs/09 P30.
- **A box on a page that scrolls smoothly prints from where the page had it.**
  The visual harvest puts back every scroll position its screenshots moved,
  because Chromium prints a scrolling box and a frame as they stand. It did so
  in a way that obeys the page's `scroll-behavior`, so on a page that sets
  `smooth` it only started an animation. From the second capture in a
  process, the MCP server's case, the PDF printed 62 to 82 ms later, with
  each box partway back and none of its text where the page had it, and no
  warning. Positions now go back at once and are read back, and one that did
  not go back is a warning. See docs/09 P39.
- **A video is photographed and printed the same however fast or loaded the
  machine is.** Chromium's controls show a loading spinner on a
  `<video controls>` for about 1.4 s after the page loads, `preload="none"`
  included, and longer when the renderer is starved. A capture that reached the
  video inside that window recorded the spinner, at an angle set by the clock,
  in the video's asset and could print it too. `animations="disabled"` does not
  reach the controls. The prepare stage now takes each paused video out of the
  page and puts it back where it was, which ends the spinner and leaves the
  bundle's snapshot as it was. The golden corpus's `lms-module` and
  `lms-module-videos` cases recorded a different asset digest from run to run
  because of it. See docs/09 P66.
- **A video whose load ends after the page is prepared is photographed the
  same way, and one still loading says so.** A `preload="metadata"` request
  that answered or failed after the prepare stage started the spinner again,
  and the harvest could photograph it in the video's asset or in that of a
  frame holding the video. The harvest now settles each video it is about to
  photograph, the visual itself or one inside it, just before the screenshot.
  A video still loading keeps its spinner, and its warning now says that its
  image shows the browser's loading indicator rather than a frame of the
  video, instead of saying its frame was preserved. See docs/09 P67.
- **Page chrome marked with an ARIA landmark role stays out of `content.md`.**
  Above a page's first heading WebShot keeps a page's header, nav, footer and
  aside out of `content.md`, `content.txt` and `chunks.jsonl`, but it knew
  them only by those four elements. A page that marks the same chrome with a
  role instead, such as `<div role="banner">` or `<ul role="navigation">`, had
  its logo, menu or disclaimer read as content. An element with the role
  `banner`, `navigation`, `contentinfo` or `complementary` now counts as
  chrome as its element does; `content.json` and `content.doctags` still hold
  it. A landmark below the first heading is unchanged. See docs/09 P71.
- **A table or picture before a page's first heading reaches `content.md`,
  `content.txt` and `chunks.jsonl`.** docling files everything above the
  first heading as page furniture, which those three skip, and WebShot moved
  only text back. A table or an image there, such as a deck's first slide
  picture or an EPUB's cover, was in `content.json` and `content.doctags`
  only, and so was the text OCR read from the image. Each is now kept out only
  if it sits inside the page's header, nav, footer or aside, and it reads as it
  would on a page with no heading. Where docling's tables or pictures cannot
  be paired with the page's to tell, the manifest says so. A cross-origin
  frame shown as its screenshot above the first heading was reported as having
  its recognized text in `content.md` and `content.txt`, which held none of
  it; the warning now says where that text is. See docs/09 P57.
- **A line break in a list item, heading, caption or code block no longer
  leaves an invisible character in the text.** docling writes each `<br>` as
  the private-use character U+E000 while it reads a page, and turns it back
  into a line break only in a paragraph or a table cell. A `<br>` that reached
  extraction in a list item such as `Jask<br>ov` therefore left U+E000
  between the two words in `content.json`, `content.md`, `content.txt`, the
  DocTags and every chunk, and so did one in a term or description, a task
  item, a heading, a table or figure caption and a `<pre>`. Captured web
  pages did not show it yet: their snapshot drops every `<br>` before
  extraction, which is a fix of its own, and this one keeps the line breaks
  that fix lets through clean. docling reads all of those but a `<pre>` onto one line, so a
  `<br>` there is now the space any other whitespace becomes, and in a `<pre>`
  it is a line break. An item or caption that held nothing but a `<br>` is
  now what docling makes of the same element empty, not an item holding only
  that character. A navigation item with a `<br>` stays in the page chrome
  instead of moving into the body, and a table caption with one is no longer
  reported lost. A page carrying U+E000 itself says so in the manifest's
  warnings, as docling deletes it from the page's text. See docs/09 P56.
- **Page chrome spelled with a curly quote, a dash or an ellipsis stays out
  of `content.md`.** A menu item, footer line or banner holding a typographic
  quote, a dash, an ellipsis, a soft hyphen or a zero-width character reached
  `content.md`, `content.txt`, `chunks.jsonl` and the body layer of
  `content.json`, while the plain items beside it stayed out. docling makes
  that punctuation plain as it reads the page; WebShot then looked for the
  plain text in the chrome as the page spelled it, did not find it, and took
  the item for content. A list item holding a line break leaked the same
  way, since docling writes the break into the item's text. Chrome is now
  compared as docling writes text, and what docling files under a `<footer>`
  counts as chrome by where it sits. A table caption with such punctuation
  no longer draws a warning that it was lost. Text before the first heading
  that is not chrome still reaches every surface. See docs/09 P58.
- **Two quotations, or two definition lists, side by side in a converted
  document stay two.** In a Word file, an EPUB, an HTML file or an HTML file
  in a zip, a `<blockquote>` straight after another reached the page as one
  quotation holding both, and a `<dl>` straight after another as one
  definition list. This held when only markup that writes no text stood
  between them, when each sat in its own `<div>`, inside a quotation or a
  definition, and across two EPUB chapters. Python-Markdown makes a
  quotation that follows a quotation part of it, and adds a term to the
  definition list before it; the comment that now keeps them apart, as it
  keeps two lists apart, is removed from the page. A document with no such
  pair is written as before, and so is a pair inside a list item. See
  docs/09 P63.
- **A Word file's lists keep the numbers Word shows on them.** mammoth reads
  a list item's level and whether it is ordered, never its number, so every
  Word list reached the page numbered from 1: one Word starts at 3 or
  restarts at 4, one it counts on past a paragraph, two instances of one list
  definition (which Word counts as one list), and two lists that meet, which
  were also joined into one. WebShot now works out each item's number from
  the file's numbering definitions, by rules measured in Word, and sets each
  list's start from it, splitting a list where Word's numbers stop running
  on; `content.md` records the same numbers. A Word file inside a zip is
  numbered too. Where the page still cannot show Word's numbering — a
  numbered heading, a list Word shows no numbers on, an item whose number
  cannot be worked out — the manifest says so. A lettered list shows its
  ordinal in digits. See docs/09 P31.
- **A dropdown's choice reaches the text surfaces.** A `<select>` dropdown
  reached `content.html` with none of its options, so the choice the page
  showed was missing there and from `content.md`, `content.txt` and
  `chunks.jsonl`; only the `form_fields` record in `assets.json` had it. A
  dropdown draws its choice and lays out none of its options, and the capture
  keeps what a reader can see by whether an element has a box. The chosen
  option now counts as shown whenever its dropdown is, a placeholder the page
  hid from the list included, and reaches every text surface as one paragraph,
  reading as the option's `label` where the page gave it a non-empty one. The
  other options stay out: the page never drew them, and the document reader
  would write each as a line of the page. A list box keeps every option it
  lays out and its `size`, and the choice is now marked on the right options
  when one before them is hidden. A hidden select's options stay out. See
  docs/09 P69, which corrects P8-75's account of the cause.
- **Two lists side by side in a converted document stay two lists.** Where an
  EPUB chapter or an HTML file in a zip had one list straight after another of
  the same kind, the page showed one list: numbered on from the first, so a
  second list that began at 1 was numbered 3, with every item spaced as a
  paragraph, and `content.md` recorded the same numbers. This held whatever
  stood between the lists if it wrote no
  text (an empty `<div>` or `<p>`, a comment, a line break), for lists in
  neighbouring `<div>`s, in a quotation or a definition, and for a chapter
  that ended with a list followed by one that began with one. markdownify puts
  only a blank line between two lists, and Python-Markdown reads that as one
  list. The two now keep their own numbering and close spacing, and the
  comment that separates them is removed from the page. A `.md` file is
  rendered as before. See docs/09 P32.
- **A converted document's pictures are in the page.** MarkItDown wrote a
  slide's picture as a made-up file name, which the page looked for beside the
  deck: the request failed, or another file of that name was captured as the
  picture, with no warning. Word pictures were cut short and drawn broken, and
  an EPUB's pictures were looked for beside the book. Each picture is now the
  document's own bytes, a TIFF is converted to PNG, and a picture no browser
  draws (WMF, EMF), one a deck only links to, or one a book names but does
  not hold is a line of text in the page and a warning naming where it was.
  For a local file, `assets.json` records such a picture's `data:` URL by type
  and length, since the bytes are in `source/`. See docs/09 P36.
- **A slide's text box with an equation in it reaches the page.** PowerPoint
  wraps such a box in `mc:AlternateContent`, beside a picture of it for older
  readers, and python-pptx reads neither, so the slide came out as its title
  alone with no warning. WebShot now rewrites the deck before MarkItDown reads
  it: the box itself in place of the wrapper, each equation as LaTeX through
  the converter MarkItDown uses for Word, SmartArt as its text, and the
  characters of an equation the converter refuses, with a warning. Word
  equations are converted one at a time, so one MarkItDown cannot read no
  longer takes every other equation in the file with it. Embedded objects,
  chart types python-pptx cannot read, and Word charts and SmartArt are named
  in the manifest's warnings. If MarkItDown cannot open the rewritten file,
  WebShot reads it as it was and says so. See docs/09 P27.
- **A deck or Word file inside a zip reads as it does on its own.** The zip
  reader handed each member straight to MarkItDown, so a zipped deck lost its
  wrapped text boxes, equations and SmartArt, and a zipped Word file every
  equation, with no warning. A member now goes through the same rewrite and
  fallback as a file, and its warnings reach the manifest under its path in
  the zip, as in `slides/deck.pptx: Slide 5: …`. A member WebShot refuses to
  parse is left out and named, and the rest of the zip is still read. See
  docs/09 P29.
- **A zip member WebShot cannot read is named in the manifest.** A member
  nothing reads, or whose reader failed, was left out of the page without a
  word: a PDF in a zip on every install, and a deck or Word file on an install
  without the office extra. Each is now named in the manifest's warnings, one
  warning per reason when many members share it, and a missing reader names
  `webshot[office]` rather than MarkItDown's `pip install` line. An encrypted
  or damaged member, or a CSV with a cell too large to read, costs that member
  instead of failing the whole zip. The `__MACOSX/` copies the Finder adds to
  a zip are left out and counted: read as text, they had put a file's
  quarantine record and the address it was downloaded from on the page. See
  docs/09 P33.
- **A protected document's segments are shot where the viewer is.** The
  SharePoint capture scrolls its viewer to each half of a tall page and waits
  500 ms before the screenshot. On a viewer that scrolls smoothly, the first
  scroll, from wherever the reader had left the viewer, was still moving when
  page 1 was shot: from the last page of a three-page test document, page 1's
  upper half came out 89 to 159 px out of place, with no warning. The viewer
  now moves at once, and its position is read back before every segment. One
  that is not where the segment assumes, because the viewer snapped or
  clamped the scroll, stops the capture with both positions instead of
  stitching a page that holds part of itself twice. See docs/09 P42.
- **A long table's capture time no longer grows with the square of its
  size.** docling-core rebuilt a table's whole grid for each cell it wrote
  into `content.doctags`, so a 1,000-row, 2-column CSV built four million
  cells, and doubling a table's rows quadrupled the time spent there. WebShot
  now has each table's grid built once for that export. Every file it writes
  is the same, byte for byte. A 2,000-row HTML table took 174 s to capture
  before and 28 s after. See docs/09 P41.
- **A list item that holds only a list keeps its line in `content.md`.** An
  item with no text of its own, the shape a Word document's second-level
  bullets take when no first-level bullet is above them, had no line at all:
  its list hung off the outer one. A Markdown reader then showed the list as
  code after a paragraph, ran it into the parent item's text under an item
  with text, and moved it under the item before in a numbered list. With a
  start number, the items after it were numbered one short, in `content.json`
  too. The item is now in `content.json`, empty and numbered, holding its
  list, and `content.md`, `content.txt` and `chunks.jsonl` write it as
  CommonMark does, on its list's first line (`-   - First sub-item`). Every
  other list is written as before. See docs/09 P45.
- **Words a no-break space keeps apart at the edge of bold text stay apart.**
  markdownify strips the edges of bold, italic, a link, code and the rest,
  and puts back an ASCII space only, so in a Word file, an EPUB or an HTML
  page in a zip `called<strong>&nbsp;SALT.&nbsp;</strong>We'll` reached the
  page as `calledSALT.We'll`. Any Unicode space at such an edge is now written
  as the document has it, outside the formatting: the page keeps the no-break
  space, and content.md reads it as a plain one. Output where the edges are
  ASCII spaces, or none, is unchanged. See docs/09 P43.
- **A list item whose only content is a picture or a table keeps it.** An
  item holding an image or a table and no text of its own was dropped from
  `content.json`, `content.md`, `content.txt` and `chunks.jsonl`, image or
  table and all, with no warning, and in a numbered list the items after it
  were numbered one short. A dropped image also left the page one picture
  short of its images, so the OCR text of every image on the page was left out
  of the text surfaces: a Word homework solution whose answer 2 was an image
  lost all 13 images' recognized text, the questions included. The item is now
  in `content.json`, empty and numbered, holding its picture or table, and the
  text surfaces write what it holds inside it, as CommonMark nests a block in
  an item (`-   Chart caption`, then `    <!-- image -->`). Every other list is
  written as before. See docs/09 P52.
- **A list item with text keeps its picture or table inside it in
  `content.md`.** `content.json` held them in the item, but `content.md`,
  `content.txt` and `chunks.jsonl` wrote the picture's caption and OCR text, or
  the table's rows, at the left margin under the item's line. A Markdown reader
  then read the caption as more of the item's text, ended the list at the
  `<!-- image -->` and started a second one with the next item, and read a
  table as a paragraph of pipes in the item; a Word homework solution whose
  answers sit beside their images read back as 21 lists rather than 5. A
  picture or table is now written after a blank line, indented to where the
  item's text begins (`- Answer`, blank, `  <!-- image -->`), so it reads as
  the item's. Only lists whose items hold a picture or a table change. See
  docs/09 P61.
- **A description-list term keeps the picture, table or list it holds.**
  docling read a term (`<dt>`) for its text alone and never looked inside it,
  so an image, a table or a list in a term was dropped from `content.json`,
  `content.md`, `content.txt` and `chunks.jsonl`, with no warning. A term with
  nothing else in it vanished entirely, its descriptions hung off the list as
  terms of their own, and a description holding a description list was
  dropped with it. A dropped image also left the OCR text of every image on
  the page out of the text surfaces. What a term holds is now inside the term
  in `content.json`, and a term with no text of its own is written as a list
  item with none is (`-   Chart caption`, then `    <!-- image -->`, with its
  descriptions under it). Every other list is written as before. See docs/09
  P60.
- **A word with a bold, italic, code or linked part is one word in the
  bundle.** docling spaced every formatted run from the text beside it, so
  `un<b>break</b>able` read `un **break** able` in `content.md` and "un break
  able" in `content.txt` and `chunks.jsonl`, and a link followed by a comma
  read "link ,", while the page and the PDF read "unbreakable" and "link,".
  Where nothing separates them on the page, they now meet with nothing between:
  `un**break**able`, `[link](…),`. Whitespace of any kind, a line break, and an
  element the page's CSS lays out as a box of its own (a flex item, a block,
  an inline-block) still keep them apart; the snapshot records that last case
  as `data-webshot-display` in `content.html`. The snapshot also keeps a
  `<br>` now: it dropped every one, so `t-test<br>Regression test` read
  "t-testRegression test" on every surface. See docs/09 P48.
- **A list or paragraph above a page's first heading keeps its shape.**
  docling files everything above the first heading as page furniture, and
  WebShot moved each line of text back on its own, leaving the list or
  paragraph around it behind. `content.md`, `content.txt` and `chunks.jsonl`
  then wrote a nested list flat, a blank line between its items and each
  item its own chunk, and a paragraph as one line per bold, italic or linked
  run. A run the site's menu also held, such as "Terms", was dropped from its
  sentence, and a table inside a list item was dropped. A list, paragraph or
  section above the first heading now moves back whole and reads as it does
  on a page with no heading. A menu, header, sidebar or footer there stays
  out whole, including the items with a curly quote, a dash or a line break
  that used to get through. Word, EPUB and Markdown documents that open with
  a list or a formatted paragraph before any heading had the same defect.
  Nothing below the first heading changes. See docs/09 P53.
- **A UTF-8 file whose first letter outside ASCII is past its first 64 KB is
  read.** MarkItDown guesses a text file's encoding from its first 64 KB, so
  it took such a file for ASCII, and the read failed wherever Magika had
  recognized the file as text: a CSV, TSV, JSON, JSON Lines, XML or YAML file
  on its own ended the capture with exit 2, and in a zip a Python file, a
  notebook and the rest were left out of the page. A YAML file with such a
  letter that Magika could not name, as a short one behind a byte order mark,
  was refused as a format nothing reads. A guess inside the 64 KB could be
  wrong too: a UTF-8 file of box-drawing characters in a zipped Python
  project was read as a Kazakh code page. WebShot now tells MarkItDown the
  encoding of the CSV it writes, and that a file which is UTF-8 to its last
  byte is UTF-8. A UTF-8 byte order mark is no longer put at the start of a
  text file's page as an invisible U+FEFF. A CSV or TSV file that is not
  UTF-8 is still read as UTF-8, but the manifest now says so: its accented
  letters had become U+FFFD, or other characters entirely, without a word.
  See docs/09 P35.
- **A list nested in a converted document stays nested.** markdownify
  indented a sub-list by the width of its parent's marker, two or three
  spaces, and Python-Markdown nests at four. So in a Word file, an EPUB or an
  HTML page, on its own or in a zip, sub-items became items of their parent's
  list or text in the parent item (`Alpha * sub a`), and an item's second
  paragraph left the list and took the next item with it. Nested lists are now
  indented by four at every depth, more of an item after its nested list stays
  in the item, and two lists in one item stay two. A list of one-line items is
  written as before, and a `.md` file is not touched. See docs/09 P40.
- **Code in a list item keeps its lines.** A `<pre>` in a list item of a Word
  file, an EPUB or an HTML page, on its own or in a zip, was printed as one
  line of inline code in the item's text, its line breaks and indentation
  gone, because Python-Markdown reads a code fence only at the start of a
  line and the item indents it. It is now a code block in its item at any
  depth, line for line as a `<pre>` outside a list reads, and a run of
  backticks in it is text. Code outside a list is written as before; code
  in a quotation within an item is docs/09 P47's, and code in a link or
  emphasis docs/09 P59's. See docs/09 P44.
- **A definition list inside a list item keeps all its definitions hold.** In
  an EPUB or an HTML page, on its own or in a zip, a definition list in a list
  item kept only each definition's first paragraph: the second became a code
  block in the item, and a list or a definition list nested in the definition
  became one line of its text (`d + x`) or a code block. Python-Markdown hands
  a block indented under a list to the processor that reads list items, which
  stopped at the definition list. The page renderer now reads such a block
  into the definition it is indented under, and a list in a definition is
  written after a blank line, as Python-Markdown needs, at any depth of items.
  An item that opens with a definition list whose definition opens with a list
  keeps that list's items in the definition. Every other document renders
  byte for byte as before. A `.md` file renders through the same reader, so a
  definition list in one of its items reads the same way. A `<pre>` in such a
  definition stays in it, but as inline code, which docs/09 P47 leaves as it
  was (P47-4). See docs/09 P62.
- **Code in a quotation or a definition keeps its lines.** A `<pre>` in a
  `<blockquote>` or a `<dd>` of a Word file, an EPUB or an HTML page, on its
  own or in a zip, was printed as one line of inline code, because
  Python-Markdown reads a code fence only at the start of a line and `> ` or
  `:   ` stood before it. It is now a code block in its quotation or
  definition at any depth, a quotation in a list item included, line for line
  as at the top level, and two blocks side by side stay two. Code outside
  them is written as before; code in a definition inside a list item still
  is (docs/09 P47-4). See docs/09 P47.
- **Words kept apart only by whitespace in bold text stay apart.** A Word
  file's `This<strong> </strong>course` reached the page as `Thiscourse`:
  markdownify writes nothing for bold, italic, a link or code that holds only
  whitespace, and drops a newline or a line break at such an element's edge.
  A link
  whose text is its address, or that has no target, dropped even a plain
  space at its edge, so `Retrieved from<a …> https://…</a>` ran together.
  Each run of whitespace there is now one space outside the formatting, in a
  Word file, an EPUB and an HTML page in a zip. An edge of plain spaces is
  written as before, and text in code keeps its own spacing. See docs/09 P49.
- **Terms in a row, and every definition, stay in a converted document's
  definition list.** In an EPUB, or an HTML page in a zip, a definition list
  with two terms for one definition showed the first as a paragraph before
  the list. A definition after one of two paragraphs became a definition list
  nested in that one, with its last paragraph for the term, and `content.md`
  left out the paragraphs either side of the nested list. markdownify writes
  a blank line before every term and none before a definition, and
  Python-Markdown reads both as less of a list. Terms in a row are now written
  on adjacent lines, and a definition of more than one block has a blank line
  after it when another follows. A list that already rendered right is
  written as before. See docs/09 P64.
- **A list in a quotation inside a list item stays the quotation's.** In an
  EPUB, on its own or in a zip, and in an HTML page in a zip, a list in a
  quotation in a list item was written as the item's own list, with no blank
  line after it.
  So text after it in the quotation ran into its last item (`xtail`), and a
  second list after it became that item's text, or more of its items when it
  was of the same kind. Such a list is now written as one in a quotation in no
  item is, at any depth of items and quotations. In a table cell, where a list
  is text, the change stops at the cell, and words that docs/09 P62's change
  had run together in some cells are kept apart again. Every other document is
  written byte for byte as before. See docs/09 P74.
- **A line break in a converted document is a line break on the page.**
  markdownify writes a `<br>` as Markdown's hard break, two spaces and a
  newline, and MarkItDown strips the end of every line, so in a Word file, an
  EPUB or an HTML page in a zip `one<br/>two` reached the page and the PDF as
  `one two`: a Word file's manual line breaks ran on as one line. Two `<br>`s
  in a list item ended the list and printed the rest of it as text, and two in
  bold text printed its `**`. The bridge now writes `<br>` itself wherever text
  flows: a paragraph, a list item, a quotation, a table cell, a definition, a
  link or formatted text. In a heading it is still a space, because the PDF's
  bookmark for the heading would run its words together; in a code block it
  is still a newline, and in inline code a space, as any line break in inline
  code is; and two or more in a row in a paragraph are still a paragraph
  break, which content.md keeps where docling would drop the `<br>`s beside
  bold text. content.md writes a line break as docling writes any page's
  `<br>`: a hard break in a paragraph, and a space in a list item. See docs/09
  P77.
- **A link whose text is its target keeps its text.** In a Word file, an EPUB
  or an HTML page in a zip, `See <a href="report.pdf">report.pdf</a> now`
  reached the page, `content.md` and the PDF as `See  now`. markdownify writes
  such a link as `<report.pdf>`, which Python-Markdown makes a link only for
  an http, https or ftp address and otherwise reads as an HTML tag. nh3 then
  dropped the tag and the text with it. A target that named a tag did worse:
  `em` made the rest of the paragraph italic, `br` became a line break, and
  `script` or `style` took everything after it in the document. Every link is
  now written `[text](target)`, as one whose text differs already was, and an
  http or https link renders the same page as before. A link in code, `kbd`
  or `samp` now reads as its text, where the page showed its Markdown,
  `[text](target)`, or the tag. See docs/09 P75.
- **A protected document's pages are each captured at their own size.** The
  SharePoint capture shot every page where page 1's position and size, and
  the step from page 1 to page 2, put it. In a document whose pages differ, a
  landscape page among portrait ones, a Legal page, an appended form or a
  short last page, every page after the first that differed came out page
  1's size, holding parts of its neighbours, with no warning. Each page is
  now found by its own `Page N.` element, scrolled to if the viewer has not
  drawn it, measured in view, and shot in one piece or two as its own height
  needs. A page the viewer cannot show whole stops the capture. The packed
  PDF gives every page the scale of the document's commonest page, where a
  landscape Letter page among portrait ones had come out 13.33 × 10.30 in.
  `capture.json` reports `page_width` and `page_height` only when every page
  shares them, and lists each page in a new `page_images`. See docs/09 P46.
- **A protected deck's slides are each captured at their own size.** The
  SharePoint capture pages a deck through the viewer's toolbar at 75% zoom,
  and clipped every slide at page 1's rectangle. A slide of another size, or
  a portrait page in the deck, came out page 1's size: a smaller one with the
  viewer's background beside and below it, a taller one cut off, with no
  warning. Each slide is now measured by its own `Page N.` element after the
  toolbar has gone to it and shot as itself, and one the viewer does not show
  whole stops the capture. Whether a document is captured as a deck or by
  scrolling is now read from page 1, scrolling the viewer to its top if page
  1 is not drawn, rather than from the first page the viewer had drawn,
  which in a viewer that keeps only the pages in view is the one the reader
  left it on. For a deck, `capture.json`'s `page_width` and `page_height` are
  now device pixels read from the images, and null when the slides differ,
  as for a scrolled document, with each slide in `page_images`. See docs/09
  P51.
- **A word with a part in a `<span>` is one word in the bundle where the page
  draws it as one.** docling merged the text of a `span`, `mark`, `small`,
  `abbr`, `cite` and every other inline element it does not format with the
  text beside it, with a space, so `un<span>break</span>able` read "un break
  able" in `content.md`, `content.txt`, `chunks.jsonl` and `content.json`; a
  citation's `<span>[</span>2<span>]</span>` read "[ 2 ]", and highlighted
  code, a span per token, read `def f ( x ):`. The snapshot now measures each
  place such an element touches the text beside it, and records in
  `content.html`, as `data-webshot-touches`, where the page draws the two with
  no gap between them; they join there and only there. A margin, a padding, a
  border or generated content keeps them apart, so a date line's `Article • 12
  Oct 2026`, drawn with margins around the bullet, is not run together into
  "Article•12 Oct 2026". A page range that wraps after its dash is still one
  range. A `content.html` captured before this change records no such
  evidence, and its spans read as before. Two things that had leaned on that
  space are fixed with it: page chrome whose text a span joins stays out of
  the body, and the snapshot keeps a space that ends a line in a span of its
  own, which it dropped as having no width, so `kelvar<span> </span>dostrel`
  read "kelvardostrel" wherever the line wrapped there. See docs/09 P70.
- **Description lists and list items read the same on docling-slim 2.137
  as on 2.132.** 2.137 reads a description list through the `<div>`s around
  its terms, a hidden one too, and makes an item of a list item holding only
  a picture or a table. A group marked `aria-hidden` stays out of the record
  on both; WebShot's own ungrouping runs only on a backend that does not read
  groups, and its reading of a description beside its list follows the
  groups. On a page carrying U+FDD2, the warning says an item's picture or
  table is left out only where docling leaves it out. See docs/09 P52-7 and
  P72-6.
- **A description list whose terms are grouped in `<div>`s keeps them.** HTML
  lets a `<div>` group a term with its descriptions
  (`<dl><div><dt>Brisk</dt><dd>Callow</dd></div></dl>`), the shape GOV.UK's
  summary list and many design systems use. docling read only a description
  list's own `<dt>` and `<dd>` children, so every group was dropped from
  `content.json`, `content.md`, `content.txt` and `chunks.jsonl`, the terms'
  and descriptions' text included, with no warning; an image in one also left
  the OCR text of every image on the page out of the text surfaces. A grouped
  list now reads exactly as the same list without its groups, on every
  surface. A group marked `aria-hidden` stays out, as a hidden term does. See
  docs/09 P72.
- **A description holding text and a description list keeps its text.** A
  description (`<dd>`) whose own child is a description list lost its text,
  before the list, after it or in a paragraph, from `content.json`,
  `content.md`, `content.txt` and `chunks.jsonl`, with no warning: docling
  hung the inner list off the term and read nothing else of the description.
  With no term above it, the inner list went too. The description is now an
  item with its text under its term, its list nested under it (`- **Term**`,
  `    - Description`, `        - **Inner term**`), as a description is
  written when its list sits in a `<div>`; a picture or a table beside the
  text is the description's too. A description holding only the list, under
  a term, is written as before, and so is every other list. See docs/09 P73.
- **Clean mode hides the ad units an ad network writes into the article.** On
  a statistics site's calculator page, an in-article video player ("My Latest
  Videos") sat inside the main content, so selecting the region kept it. The
  text recognized in a screenshot of its ad frame was printed between the
  calculator's result and its verdict. Clean mode now hides Mediavine's video
  slot and player, Google's video-ad frame, frames and videos that title
  themselves "Advertisement", AdSense units and Publisher Tag slots. Faithful
  mode keeps them. See docs/09 P23-9.
- **A list item's code keeps its lines in `content.md`.** A block of code
  (`<pre>`) in a list item on the page reached `content.md`, `content.txt` and
  `chunks.jsonl` as one line: inline code on the item's line, or, for an item
  that opens with its code, the item's own text with no code at all. The
  code is now a block in its item, each line indented into the item at any
  depth, after a nested list as before one, with a fence longer than any run
  of backticks in the code. `content.json` holds it as a code item under the
  list item, with its lines. A list with no code in its items is written as
  before. See docs/09 P50.
- **A heading in a page's footer no longer brings the footer into the text.**
  docling files a `<footer>` as page chrome, but a heading inside one, such as
  a site footer's "Company" or "Resources" column, put the heading and the
  rest of the footer into `content.md`, `content.txt` and `chunks.jsonl`, and
  the text after the footer was chunked under the footer's last heading. On
  three of ten public pages captured in faithful mode, and one of them in
  clean mode too, the footer was from a third to nearly two thirds of
  `content.md`. Everything inside a footer now stays out of those files and
  in `content.json` as chrome, headings included. So do a footer's pictures,
  and the alt text, empty list items and rewritten dashes that got through
  even without a heading. A picture in a footer keeps its figure chunk. See
  docs/09 P65.
- **Code keeps its lines, its indentation and its fence.** A block of code a
  highlighter wrote (GitHub, Pygments, highlight.js, Prism, Shiki,
  Docusaurus), with an element for each token, reached `content.md`,
  `content.txt`, `chunks.jsonl` and `content.json` with most of its line
  breaks and all its indentation gone and a space between tokens
  (`def f ( x ): return x`). Code with a link or emphasis in it was written as
  a run of inline code on one line, the first line of any block lost its
  indentation, and a line of three backticks in the code ended the block. A
  line-number table (Pygments, Rouge, Chroma) was a table row with the code on
  one line, and the snapshot dropped every blank line of Chroma's code. Each
  `<pre>` is now one code item holding the text a reader selecting it gets,
  less the blank lines at its edges, written in a fence longer than any run of
  backticks in it; a line-number table is its code. Code no highlighter split
  and that does not start indented is written as before. See docs/09 P55.
- **A code block's inline line numbers stay out of its code.** Sphinx's
  numbered blocks (Pygments) and Hugo's `linenos=inline` (Chroma) write each
  line's number at the start of the line, set apart with padding and marked
  unselectable, and `content.md`, `content.txt`, `chunks.jsonl` and
  `content.json` read it as the line's first characters: `1package main`, `2`,
  `3import "fmt"`. The snapshot now leaves out such a number, only digits at
  the start of a line in a `<pre>` that holds code besides, and the manifest's
  warnings name each block and the numbers it showed. Prompts the page marks
  unselectable the same way (`>>> `, `$ `) stay, numbers a reader can select
  stay, and a line-number table is read as before. See docs/09 P68.
- **A page that enforces Trusted Types is captured.** A site that sends
  `require-trusted-types-for 'script'` made the bundle stage throw on its
  first `DOMParser` call, and the capture exited 5. Large video sites' watch
  pages do this. The inert copy the bundle reads is now made without parsing
  a string. See docs/09 P23-1.
- **A page that sends the browser to its own site's home page is named.** A
  page that answered a signed-out browser by moving it to the site's root
  was captured as that page, with only the "nearly empty" warning. A
  manifest warning now says the capture is of the home page. See docs/09
  P23-5.
- **A frame or a picture the page removes no longer ends or stalls a
  capture.** An ad frame removed while clipped text was being released ended
  the capture with exit 5, and a picture removed while the bundle was being
  built made the harvest wait five minutes for it, once per picture. The
  frame is skipped, the picture costs at most 5 s and is named in a warning,
  and an asset is listed only once it is fully captured. See docs/09 P23-2.
- **A bot check is no longer reported as a sign-in wall.** A 403 from
  Cloudflare's challenge (`cf-mitigated: challenge`) or from DataDome
  (`server: DataDome`) now says a bot check refused an automated browser, and
  that saving the page from your own browser and capturing the file works,
  instead of asking for `--storage-state`. A 403 that names no check gives
  both readings. Still exit 4. The failure report records the HTTP status and
  final URL of every refused status, where it said null. See docs/09 P23-3.
- **A redirect to a sign-in page on another site exits 4.** The spec promised
  this since 3.0, and only a 401 or 403 did it: a site that sent a browser with
  no session to its identity provider was captured, sign-in form and all, with
  exit 0. The run now exits 4 when the page it lands on is on another origin
  and shows a password field, and says to pass `--storage-state` or
  `--auth-profile`. A `--wait-for` that times out on such a page is exit 4 too,
  not 3. A sign-in page asked for directly is still captured. See docs/09
  P22-1.
- **An empty capture is no longer a silent success.** A page that rendered no
  image, no video and fewer than 50 characters of text, such as an app shell
  whose script never ran, now carries a manifest warning saying so. The new
  `--require-content` (also `[capture] require_content`) makes it exit 5
  instead, with nothing published. See docs/09 P14-1 and P22-2.
- **A subscript or superscript stays on the word it belongs to.**
  `H<sub>0</sub>` read "H0" in the PDF's text layer but "H 0" in `content.txt`
  and every chunk, and `**(H** **0** **)**` in `content.md`, because the
  docling backend and serializers join inline parts with a space. The bridge
  now carries "nothing separated these" through the conversion, so the text
  surfaces read "H0" and a search finds the bundle as well as the PDF;
  `content.json` still records the script. See docs/09 P14-56.
- **A page's own U+FDD0 or U+FDD1 survives being written as a reference.**
  Given HTML with `&#xFDD0;` or `&#64977;`, the docling bridge read the
  character as one of its join markers and removed it, with no warning. It
  now keeps the page's character and gives the warning a page carrying the
  raw character gets. A capture was not affected: the browser and the
  sanitizer decode the reference before the bridge sees it. See docs/09 P54.

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
- **The same bump no longer fails on the machine that recorded the goldens.**
  The strict profile still compared those two versions, and the recorded
  environment does not include them, so an OCRmyPDF update kept that machine
  on `strict` and failed there. Both profiles now mask them. See docs/09
  P20-12.
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

- **A consent manager's dialog no longer prints over the page, in either
  mode.** A news article's Cookiebot dialog was printed on five of its six
  pages, over the text, and its words were read into `content.md` in the
  middle of the article's sentences, in faithful and clean mode alike: the
  dialog was narrower than clean mode's overlay rule and named nothing its
  cookie selectors look for. Both modes now hide the dialogs of seven consent
  managers by the ids their vendors give them (Cookiebot, OneTrust, Didomi,
  Quantcast Choice, TrustArc, Usercentrics, Sourcepoint), and a manifest
  warning names each one that was on screen. This is the one thing faithful
  mode now hides. See docs/09 P79.

- **A fixed or sticky box printed over the page's text is now named in a
  warning.** Chromium prints a box fixed to the window on every page, so a
  site's pinned header or notice bar covered lines of text page after page,
  and a sticky bar stuck to the window's bottom edge landed on the first page
  over its text. Nothing said so. A news site's privacy banner covered 1,549
  characters on 10 of 26 pages of a faithful capture. After the render,
  WebShot now prints the page a second time with each such box in a colour of
  its own and reads the result back with PDFium, in paint order, so it counts
  only text the box is drawn over: a line the page raises above the box, a box
  too faint to hide anything, and a box over blank margin are not reported.
  One warning per box names it, the pages where it covers text, how many
  characters and lines, and the first words covered, and suggests the
  `--exclude` that leaves it out. pypdfium2, already installed through
  OCRmyPDF, is now a direct dependency for this. See docs/09 P83.

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
