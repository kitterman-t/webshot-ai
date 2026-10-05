# Troubleshooting

## Start with `doctor`

```bash
webshot doctor
```

```
PASS  Chromium             151.0.7922.34
PASS  Tesseract            tesseract 5.5.3
PASS  Tesseract languages  eng available
PASS  Ghostscript          10.7.1
PASS  veraPDF              veraPDF 1.30.0
PASS  Extraction           docling-slim 2.120.3, docling-core 2.92.0, chonkie 1.7.0, nh3 0.3.6
PASS  Local formats        markitdown 0.1.7, trafilatura 2.2.0; office formats: docx, pptx, xlsx
PASS  Output directory     output/pdf is writable

Everything WebShot needs is installed.
```

Each line is `PASS`, `OPTIONAL`, `WARN`, or `FAIL`, and anything that is not
`PASS` prints a one-line fix. `OPTIONAL` means a tool that only an opt-in flag
uses is not installed (Ghostscript for `--pdfa`, veraPDF for `--validate-pdf`).
`doctor` exits `0` unless a check is `FAIL`; a `WARN` is a degraded capability,
not a broken install.

```bash
webshot doctor --json                      # the same report as data
webshot doctor --ocr-language eng+deu      # check a language pack you will need
webshot doctor --output /srv/captures      # check a directory you will write to
```

| Check | Needed for |
|---|---|
| Chromium | everything |
| Tesseract, language packs | text recognition inside images; without it OCR degrades to a warning |
| Ghostscript ≥ 10.7 | only `--pdfa` (OCRmyPDF reports JPEG-corrupting bugs in 10.6; Linux distribution packages are often older, so take a release from ghostscript.com) |
| veraPDF, or a running Docker daemon | `--validate-pdf` |
| Extraction stack | the AI bundle |
| Local formats | non-HTML local documents |
| Output directory | publication |

## Exit codes

WebShot never exits `1` for a classified failure — each code means one thing, so
a script can tell a bad flag from a timeout.

| Code | Meaning | Usually |
|---|---|---|
| 2 | usage or configuration error | a bad flag combination, a missing `--css` file, an invalid `webshot.toml`, or a second run against an output path already locked |
| 3 | navigation or readiness failure | the page never loaded, the host was unreachable, or `--wait-for` never matched |
| 4 | authentication required, or the auth material was rejected | the page answered 401 or 403, or the site redirected to a sign-in page on another site |
| 5 | capture integrity failure | the protected page-count invariant did not hold, or `--require-content` met an empty capture |
| 6 | OCR was required and unavailable | `--require-ocr` with no working engine |
| 7 | PDF render or validation failure | `--validate-pdf=strict` against a non-conformant file |
| 8 | bundle build or publish failure | the extraction bridge could not parse the snapshot |
| 9 | environment failure | run `webshot doctor` |

Every code is live as of v3.0: nothing exits `1`, and Ctrl-C exits `130`.
[The specification](../04-spec.md#3-exit-codes-must) is normative, and
`webshot --help` prints the same table.

**A failing run still writes its `--report` file.** It carries the process's own
`exit_code` and an `error` string saying what went wrong, with `null` for the
fields the run never reached. On a successful run the `error` key is absent
entirely, so `"error" in report` is the test — that file is the one to attach to
a bug report about a capture that did not happen.

## Common situations

### The run stops with "The server returned HTTP 404"

WebShot refuses to turn an error page into a capture, so a 4xx or 5xx answer
exits `3` (or `4` for 401 and 403) and publishes nothing. Check the address.
If the error page is what you want, `--allow-http-errors` captures it, and the
manifest records the status in `source_response.http_status`.

### Every HTTPS page fails with `ERR_CERT_AUTHORITY_INVALID`

The network is probably inspecting TLS through a proxy whose certificate
authority the browser does not trust, which is common on corporate networks and
in sandboxes. `curl` can succeed where the browser fails, because the two read
different certificate stores. Chromium on Linux reads the NSS database in
`~/.pki/nssdb`; on macOS and Windows it uses the system store. Add the proxy's
CA certificate there, or run WebShot on a network without the proxy.

### The capture is missing the page's images

Check `warnings` in the manifest or the QA report. `--max-assets` (default 50)
caps how many visuals are kept. It counts only visuals the page shows at 32 by
24 pixels or larger, since hidden elements and small icons are skipped, and
the warning says how many the cap left out. Raise it, or narrow the capture
with `--selector`.

### The page is cut off part-way down

Lazy-loading pages need the scroll pass. If the manifest warns that scrolling
"reached the safety limit", raise `--max-scrolls`, or slow it with
`--scroll-delay` so content has time to load.

### Content that loads late is missing

WebShot does not wait for the network to go quiet, because analytics,
streaming and long-polling connections can keep a page busy indefinitely. It
scrolls for lazy-loaded content, then waits up to 30 seconds for web fonts and
for every image that can print. A lazy image the scroll did not reach is
loaded then too; an image the page does not show is not waited for. If the
wait runs out, the warning names the images still loading. If a page fills in
after that, name an element that appears only once it is ready, such as
`--wait-for "[data-dashboard-ready='true']"`; a selector that never matches
fails the run with exit 3.

### The PDF does not look like the page

`--debug-screenshot prepared.png` saves a full-page screenshot of the page as
WebShot prepared it, just before printing. If the screenshot is wrong too, the
problem is in preparation: try `--mode faithful`, or a different `--selector`
or `--exclude`. If the screenshot is right and only the PDF is wrong, the
problem is in print pagination, and the sections below are the place to look.

### Lines are cut off at the side of the page

If the manifest warns that "text runs off the printed page", the page's print
stylesheet lays something out wider than the paper, and the PDF loses whatever
falls past the edge. The bundle's `content.md` still has that text, because it
is read from the page rather than from the render. The warning names the box
that runs off, often a grid column such as `col-sm-8 col-sm-push-4`. Pass
`--css` with a print rule that puts that layout back on the page, for example:

```css
@media print {
  .container { width: auto !important; }
  [class*="col-sm-"] { float: none !important; width: auto !important;
                       left: auto !important; right: auto !important;
                       margin-left: 0 !important; }
}
```

The warning goes away once nothing is cut. `--scale` alone usually does not fix
this, because a layout that is pushed or centred moves with the page width.

A second warning, "text is hidden in a scrolling box", means a box that
scrolls on screen held text that was not scrolled into view. A PDF cannot
scroll, so it prints only the part that was showing. WebShot already lets a
box capped by its own height grow for print whenever it can do that without
covering anything, so a capped log usually prints whole with no warning. It
does the same for a table that scrolls sideways, whenever the table fits the
page once its cells may wrap. It also does it for a web app's layout, where
one pane scrolls inside a frame the size of the window: the pane and the
frame around it are released together, so the whole document prints rather
than one screenful. A dashboard panel in a grid of fixed-height rows is
released too, with the grid's rows left to their content. A panel positioned
over the page, such as a floating contents list or a chat widget, is moved
into the page where it sits in the document, and the capture log says so. All
of this happens inside frames from the page's own site as well. The
warning is for the rest: a table too wide even wrapped, any release that
would have covered neighbouring content, and boxes inside a frame from another
site that WebShot chose to leave: a frame with a sign-in or payment field, or
one too short to show the box grown. The capture log says which it was. For a
box inside a frame, the warning names the frame too, as in
`div#log in iframe[title="Desk log"]`. A third warning, "the release did not
hold", means WebShot changed a frame from another site, and the printed page
showed the change did not work. `--css` cannot reach inside another site's
frame, so that warning is a record, not a thing to fix. The warning names the box. Let it grow or
wrap for print, for example:

```css
@media print {
  .table-wrap { overflow: visible !important; }
  .table-wrap :is(th, td) { white-space: normal !important; }
  .log { max-height: none !important; overflow: visible !important; }
}
```

If the table is still too wide once it wraps, the page-edge warning above
names it instead. A wide table, unlike a pushed or centred layout, gets more
room from a smaller `--scale`. In WebShot's own twelve-column test page,
`--scale 0.7` with the CSS above cleared both warnings.

### A warning says the text layer has ligature characters

WebShot prints every page with ligatures off. Otherwise Chromium records "fi"
in the PDF's text as the single character "ﬁ", and a search for "first" misses
the word. The warning means some text escaped that step. Usually it sits
somewhere WebShot cannot reach: a closed shadow root, a frame it could not
script, or a pseudo-element with its own `font-feature-settings`. Less often,
the page wrote the character itself. The warning names the characters found.
The bundle's `content.md` is read from the page rather than from the PDF, so
wherever it has the passage, it spells the words out. Text inside a shadow
root or a frame may not be in the bundle at all.

### Nothing was recognized inside the images

`ocr.engine` in the manifest is the engine that actually recognized something. If
it is `null`, either there was nothing to recognize or the engine could not run —
`webshot doctor` distinguishes the two. On a machine where Tesseract cannot be
installed:

```bash
uv sync --extra ocr-rapid    # name any other extras you use too
webshot https://example.com --ocr-engine rapid
```

`--ocr-psm` is a Tesseract setting; under `--ocr-engine rapid` it is warned and
ignored, and under `--protected-viewer` the engine choice itself is warned and
ignored, because that path's text layer comes from OCRmyPDF and its word
coordinates are Tesseract TSV.

### The wrong part of the page was captured

Look at `content_discovery` in the manifest — it names the strategy that chose
the region (`explicit`, `trafilatura`, `static-selectors`, `full-page`). If
`--auto-selector` picked badly, say what you want:

```bash
webshot https://example.com --selector "main article" --exclude ".related"
```

### `--pdfa` was refused

PDF/A conversion strips the accessibility tags Chromium builds, so it is
available on `--protected-viewer` captures only. WebShot refuses rather than
silently trading accessibility for archival conformance
([ADR-0010](../adr/0010-validation-gates.md)).

### `--validate-pdf` says conformance was "not established"

Two different things both report that, and the `detail` says which: either no
validator was found (install veraPDF, or start Docker so `verapdf/cli` can run),
or the file declares no PDF/A conformance level, so there was nothing to
validate. A gate that could not run has not found a problem, which is why
neither case fails even under `strict`.

### A second run says the output path is locked

A per-output lockfile makes a concurrent run against the same output exit `2`
rather than interleaving two captures into one file. Wait for the first to
finish, or give the second its own `--output`.

### A config file is being ignored

It is being ignored on purpose if you did not name it. WebShot reads a settings
file only from `--config` or `WEBSHOT_CONFIG`; there is no `./webshot.toml`
discovery, for the reasons in [Configuration](configuration.md).

## MCP problems

### The client says the server failed to start

Run the client's exact command in a terminal; the reason is printed on stderr.
The usual causes are a `command` that is not the full path to
`.venv/bin/webshot` in your clone, a `--config` file that does not exist (exit
`2`), or the `mcp` extra missing from the environment (exit `9`, with the
`uv sync` command to fix it). A client starts the server in a directory of its
own choosing, so give `output_root` and `roots` as full paths
([setup](mcp.md#set-it-up)).

### The client times out mid-capture

`capture` runs a browser, an OCR pass, and a PDF composition. Set the client's
request timeout above the server's operation timeout (five minutes by default).
Progress notifications are sent throughout, so a capture that is working looks
different from one that has hung.

### `capture` refuses a localhost URL

That is the default policy: MCP captures refuse private, loopback, and
link-local targets. An operator who wants localhost captures opts in with
`[mcp] allow_private_networks = true`. The CLI is unaffected.

### A read tool says a path is outside the allowed roots

The server reads only inside `[mcp] roots`, plus its own `output_root`.
`webshot mcp --config … --print-roots` prints exactly what it will accept.

### `capture` refuses an authentication profile

Either the name is not in `[mcp.auth_profiles]`, or the profile has never been
signed in to. The MCP server cannot open a headed browser; run the
`--interactive-auth` command it prints, once, in a terminal.

## Reporting a bug

Include the QA report:

```bash
webshot <source> --report report.json --verbose
```

It carries the options as they resolved, the timing of each stage, the counts,
every warning, and which validation gates ran — which is usually enough to
reproduce the problem without the page itself. Check it for anything sensitive
before attaching it; it records the source URL and the final URL.
