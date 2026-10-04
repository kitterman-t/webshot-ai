<!--
  GENERATED FILE — do not edit.
  Source: tools/docs/cli_reference.py, run against the argparse parsers in
  src/webshot/. Regenerate with `python tools/docs/cli_reference.py --write`;
  tests/test_docs.py fails if this file and the parsers have drifted.
-->

# CLI reference

Every option WebShot accepts, read off the parser rather than transcribed.
The compatibility story — which flags came from v2.2 and which are new — is in
[the specification](../04-spec.md#11-cli); this page is what the tool does
today.

```
webshot SOURCE [options]
webshot doctor [options]
webshot schema [NAME] [--write DIR]
webshot mcp [options]
webshot journey URL --dry-run [options]
```

`SOURCE` is an `http(s)` URL or a local file. Local documents that are not HTML
are converted first; the formats are listed in
[the quickstart](quickstart.md#local-documents).

Settings can also come from a TOML file and from the environment, in the order
**flags > `WEBSHOT_*` > `--config` file > defaults** — see
[Configuration](configuration.md).

## `webshot SOURCE`

| Argument | What it is |
|---|---|
| `source` | HTTP(S) URL, or a local HTML, Markdown, JSON/JSONL, CSV/TSV, text, XML/YAML, EPUB, ZIP, DOCX/PPTX/XLSX (office extra), or image file |

| Option | Value | Default | What it does |
|---|---|---|---|
| `--version` | — |  | show program's version number and exit |
| `-o`, `--output` | `OUTPUT` |  | output PDF path; a file already there is replaced (default: output/pdf/NAME.pdf under the current directory, with NAME made from the web address or the file name) |
| `-s`, `--selector` | `SELECTOR` |  | CSS selector for the content to keep |
| `--auto-selector` | — | off | automatically isolate the most substantial semantic content region |
| `--mode` | `clean` \| `faithful` | `clean` | clean removes common page chrome; faithful preserves the page structure |
| `--exclude` | `SELECTOR` |  | CSS selector to omit (repeatable) |
| `--wait-for` | `SELECTOR` |  | wait for an element before capture |
| `--delay` | `DELAY` | `1.0` | seconds to wait after initial load |
| `--timeout` | `TIMEOUT` | `120.0` | navigation timeout in seconds |
| `--no-scroll` | — | off | skip lazy-loading scroll pass |
| `--max-scrolls` | `MAX_SCROLLS` | `100` | lazy-load scroll safety limit |
| `--scroll-delay` | `SCROLL_DELAY` | `0.25` | seconds between scrolls |
| `--format` | `Letter` \| `Legal` \| `Tabloid` \| `A3` \| `A4` \| `A5` | `Letter` | paper size |
| `--landscape` | — | off | use landscape orientation |
| `--margin` | `MARGIN` | `0.6in` | page margin |
| `--scale` | `SCALE` | `0.9` | rendering scale from 0.1 to 2.0 |
| `--media` | `print` \| `screen` | `print` | CSS media type the page is laid out with: print applies the site's print styles, screen keeps the styles shown in a browser window |
| `--no-header-footer` | — | off | omit title, source, and page numbers |
| `--title` | `TITLE` |  | override the document title shown in the header |
| `--no-tagged-pdf` | — | off | disable accessible PDF structure tags |
| `--no-outline` | — | off | disable PDF bookmarks generated from headings |
| `--prefer-css-page-size` | — | off | let the page's own @page size override --format |
| `--storage-state` | `STORAGE_STATE` |  | Playwright auth storage-state JSON |
| `--auth-profile` | `AUTH_PROFILE` |  | private persistent browser profile for protected sites; keeps sign-in local without exporting cookies to JSON |
| `--interactive-auth` | — | off | open a headed browser and pause while you complete sign-in/MFA; requires --auth-profile |
| `--protected-viewer` | — | off | capture every page exposed by an authenticated SharePoint PDF viewer, then build a searchable image-backed PDF and page-level AI bundle |
| `--css` | `CSS` |  | additional UTF-8 CSS file to inject |
| `--user-agent` | `USER_AGENT` |  | override Chromium's native user-agent |
| `--allow-http-errors` | — | off | capture the page even when the server answers with an HTTP error status (400 or above); without it such an answer fails the run with exit 3, or exit 4 for 401 and 403 |
| `--debug-screenshot` | `DEBUG_SCREENSHOT` |  | save the prepared full page as a PNG |
| `--no-ai-bundle` | — | off | only create the PDF; omit semantic text, chunks, tables, assets, and provenance |
| `--no-embed-bundle` | — | off | do not carry the bundle inside the PDF; the PDF stays a rendering and the bundle stays a separate directory |
| `--embed-assets` | — | off | also embed the binary visual assets in the PDF (larger file; the images are already visible in the rendered pages) |
| `--legacy-bundle` | — | off | also emit v2-format content.json and chunks.jsonl under legacy/ inside the bundle (listed in the manifest with legacy: true); ships in v3.0, removed in v3.1 — see docs/migration-v2-to-v3.md |
| `--ai-bundle-dir` | `AI_BUNDLE_DIR` |  | AI bundle directory (default: output path with an .ai suffix) |
| `--no-videos` | — | off | do not read the videos embedded in the page: no walkthrough steps, caption transcripts, or record of untranscribed media in the bundle, and no walkthrough pages appended to the PDF |
| `--video-assets` | — | off | also download each embedded video's step clips and source recording into the bundle (off by default: an agent cannot watch them, and the stills and narration already carry the procedure) |
| `--no-ocr` | — | off | save visual assets without running text recognition on them, with either engine (Tesseract or RapidOCR); a --protected-viewer capture still recognizes its pages, because its searchable PDF is built from that text |
| `--require-ocr` | — | off | fail with exit 6 when no OCR engine is available, instead of capturing without recognized text and warning about it |
| `--ocr-language` | `OCR_LANGUAGE` | `eng` | Tesseract language or language combination, such as eng or eng+spa |
| `--ocr-psm` | `3` \| `6` \| `11` \| `12` | `11` | Tesseract page segmentation mode; 11 works well for mixed visual assets |
| `--ocr-engine` | `tesseract` \| `rapid` | `tesseract` | text recognition engine; rapid is RapidOCR, which needs no system binary: install it from your checkout with uv sync --extra ocr-rapid (name any other extras you use too) |
| `--max-assets` | `MAX_ASSETS` | `50` | maximum visible images, charts, canvases, SVGs, and iframes to preserve |
| `--pdfa` | — | off | convert the PDF to PDF/A-3 for archiving; protected-viewer captures only, because the conversion strips the web path's accessibility tags |
| `--validate-pdf` | `report` \| `strict` |  | check PDF/A conformance of the published file with veraPDF; strict turns a non-conformant file into exit 7 (an absent validator stays a warning either way) |
| `--report` | `REPORT` |  | write capture details and warnings as JSON |
| `--config` | `CONFIG` |  | TOML settings file; flags override it and WEBSHOT_* overrides it. Only an explicitly named file is read — WebShot never picks up a webshot.toml from the working directory |
| `-v`, `--verbose` | — | off | log every stage as it runs |

## `webshot doctor`

Checks that this machine can capture, recognize, and publish, and prints a one-line fix for anything it cannot. Exit `0` when every required check passes.

| Option | Value | Default | What it does |
|---|---|---|---|
| `--ocr-language` | `OCR_LANGUAGE` | `eng` | language pack(s) a capture would ask Tesseract for, such as eng+spa |
| `--output` | `OUTPUT` | `output/pdf` | directory a capture would publish into |
| `--json` | — | off | print the report as JSON instead of text |

## `webshot mcp`

Serves WebShot's tools to an MCP client over stdio. The rules the server runs under, and how to configure them, are in the [MCP guide](mcp.md).

| Option | Value | Default | What it does |
|---|---|---|---|
| `--transport` | `stdio` | `stdio` | how the client connects; stdio is the only one |
| `--config` | `CONFIG` |  | TOML settings file holding this server's [mcp] section — roots, output root, network policy, auth profiles. WEBSHOT_CONFIG names it too; nothing is discovered from the working directory |
| `--print-roots` | — | off | print the resolved policy and exit, without serving |
| `-v`, `--verbose` | — | off | log tool calls and refusals to stderr (never stdout, which is the protocol) |

## `webshot journey`

Walks a Continu training journey, whose modules have no URLs of their own. It enumerates the tree twice and diffs the two walks, then captures exactly the module set that enumeration produced — a PDF and bundle per module, five directory levels deep. Every module typed `assessment` is recorded and never opened. `--dry-run` stops after the enumeration.

| Argument | What it is |
|---|---|
| `url` | the journey's URL — the one address it has |

| Option | Value | Default | What it does |
|---|---|---|---|
| `--dry-run` | — | off | stop after the enumeration instead of capturing what it listed |
| `-o`, `--outline` | `OUTLINE` | `outline.json` | where to write the enumeration manifest (default: ./outline.json) |
| `--storage-state` | `STORAGE_STATE` |  | Playwright auth storage-state JSON |
| `--auth-profile` | `AUTH_PROFILE` |  | private persistent browser profile for a signed-in LMS |
| `--interactive-auth` | — | off | open a headed browser and pause for sign-in; requires --auth-profile |
| `--block-private-requests` | — | off | refuse requests to private, loopback and link-local addresses |
| `--corpus` | `CORPUS` |  | directory the captured journey lands in, five levels deep (default: ./<journey-id>). Ignored under --dry-run |
| `--min-interval` | `SECONDS` | `1.0` | floor on the gap between opening one module and the next (default: 1.0), so the walk paces its requests to the journey's host. The achieved intervals are reported, because a floor beneath a cost that already exceeds it never fires |
| `--allow-module-type` | `TYPE` |  | also open modules of this type, after you have looked at one (repeatable). Only 'article' is opened by default, because a type this build does not recognise may be an assessment under another name. 'assessment' itself can never be permitted |
| `--no-resume` | — | off | capture every module again instead of skipping what a previous run of this journey finished |
| `--no-verify-determinism` | — | off | skip the second walk. The diff of two walks is the evidence that the ids are stable, so this makes the outline unusable as a capture contract |

## `webshot schema`

Prints one published JSON Schema, or lists them all when given no name.
`--write DIR` writes every schema into `DIR`, which is how the repository's
`schemas/` directory is regenerated after a contract model changes.

Available schemas: `assets`, `chunk`, `journey-outline`, `legacy-chunk`, `legacy-content`, `legacy-manifest`, `manifest`, `qa-report`, `viewer-capture`, `viewer-chunk`, `viewer-content`, `viewer-manifest`.

## Exit codes

WebShot **never exits `1`**; each code means one thing
([specification §3](../04-spec.md#3-exit-codes-must)).
Scripts that tested `$? -eq 1` must test `$? -ne 0`. Interrupting a run with
Ctrl-C exits `130`, the shell's own SIGINT convention.

| Code | Meaning |
|---|---|
| 0 | success |
| 2 | usage or configuration error |
| 3 | navigation or readiness failure |
| 4 | authentication required, or the auth material was rejected |
| 5 | capture integrity failure |
| 6 | OCR required but unavailable |
| 7 | PDF render or validation failure |
| 8 | bundle build or publication failure |
| 9 | environment failure (webshot doctor explains it) |

With `--report`, a run writes its QA report whether or not it succeeded: the
file carries the same `exit_code` the process returned, plus an `error` string
that is absent entirely from a successful report.
