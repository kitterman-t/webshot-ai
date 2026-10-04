# Bundle format 3

Every capture writes a PDF and, beside it, a directory:

```
output/pdf/<slug>.pdf
output/pdf/<slug>.ai/
```

The directory is the **AI bundle**. It is self-describing: `manifest.json`
declares `bundle_format`, so a consumer written for v2 can detect v3 by that
field alone, and a consumer written for v3 never has to guess.

## What is in it

```
<slug>.ai/
├── manifest.json          provenance, counts, checksums, tool versions
├── content.json           the document, losslessly — a DoclingDocument
├── content.md             the document as markdown
├── content.txt            the document as plain text
├── content.html           the document as sanitized HTML
├── content.doctags        the document in docling's tag format
├── chunks.jsonl           one retrieval-sized chunk per line
├── assets.json            visual assets, form fields, embedded media
├── assets/                the images themselves (PNG)
├── tables/table-NNN.csv   one CSV per table
├── links.json             every link, with its text
├── structured-data.json   the page's JSON-LD
├── accessibility.yaml     the accessibility tree as captured
├── videos/<id>/           one directory per video whose words were obtained
└── source/                a byte-for-byte copy of the input, for a local file
```

A `videos/` directory holds one subdirectory per video that contributed text.
A walkthrough's is named by its playbook id and holds `walkthrough.md`,
`steps.json`, `transcript.txt` and `transcript.vtt`. A plain `<video>` or
`<audio>` gets `media-NN` in the order it appeared, holding `transcript.txt`
and `transcript.vtt` when the page shipped a readable `kind="captions"` track.
A `subtitles` track is stored beside them as `translation-<lang>.vtt` and is
**not** a transcript: it is a translation of the speech rather than a record
of it, so it is never inlined into `content.md` and never chunked. The
manifest's `tracks` array names each track's `role` so the distinction never
has to be re-derived from a filename.

A protected-viewer capture writes a smaller, page-oriented bundle:
`manifest.json`, `capture.json` (the policy record), `content.json` with one
entry per captured page, `content.md`, `content.txt`, `chunks.jsonl`, `pages/`
holding each captured page image as a lossless PNG, and `ocr/` holding the
text layer and per-page word coordinates as Tesseract TSV.
[Signed-in pages and protected documents](signed-in.md) describes the PDF that
comes with it.

Not every file appears in every bundle: a page with no tables has no `tables/`,
and `--no-ai-bundle` writes no bundle at all.

### Embedded-video capture needs the bundle

This is a real limitation, not an oversight, and it is stated here because a
warning during the run is not documentation. Walkthrough capture writes four
files per video *into the bundle* — `walkthrough.md`, `steps.json`,
`transcript.txt`, `transcript.vtt` — and the PDF appendix is composed from
them. With no bundle to stage into there is nowhere for that work to land, so
`--no-ai-bundle` skips video discovery, the appendix, and the
`videos`/`video_tally` records together. The captured page is unaffected: an
embedded player renders in the PDF exactly as it appears on the page, which
for most players is a poster frame.

The run says so — `ignored_option_warnings` emits *"Embedded videos were not
read"* whenever `--videos` is on and there is no bundle — so a capture of a
page whose procedure lives in a player is never silently indistinguishable
from a page that had none. But the warning describes behaviour this page
previously did not, which is the half that was missing.

If you want the walkthroughs and not the sidecar directory, capture with the
bundle and let `--embed-bundle` (the default) carry it inside the PDF; the
bundle directory is then a by-product you can delete. `--no-videos` is the
flag that means "do not read the videos".

## Start here

For most consumers the entry points are two files.

**`content.md`** is the whole document as markdown — headings, paragraphs,
lists, tables, and code, in reading order. Tables are GitHub-flavored Markdown
pipe tables with no padding inside the cells, so a long cell does not widen
every row; `content.txt` and the chunks write them the same way.

**`chunks.jsonl`** is one JSON object per line, sized for retrieval:

```json
{
  "id": "ch_000002",
  "text": "WebShot Quality Review\nExecutive summary\nProfessional web-to-PDF…",
  "meta": {
    "doc_items": ["#/texts/3"],
    "headings": ["WebShot Quality Review", "Executive summary"],
    "kind": "text",
    "page": null,
    "locator": "#/texts/3",
    "sha256": "…"
  }
}
```

`doc_items` and `locator` are anchors into `content.json`, so a chunk can always
be traced back to the document element it came from. `kind` is `text`, `table`,
or `figure`. `page` is a real page number on the protected-viewer path and
`null` on the web path, which has no print-pagination mapping from the DOM.

Chunks are produced by docling's `HierarchicalChunker` and then size-split by
chonkie at a character budget — no tokenizer, no model download, nothing fetched
at runtime.

Every heading reaches a chunk. A heading whose section has no body of its own
(one followed directly by a heading at the same or a higher level, or ending
the page) gets a chunk whose `text` is its heading path alone, with `doc_items`
and `locator` pointing at that heading.

### Recognized text is marked

Text that OCR read out of an image, chart, canvas or frame screenshot is not
text the page wrote, and it can be noise. Wherever it appears in `content.md`,
`content.txt` or a chunk, it stands between two lines that say so:

```text
[OCR text from asset-003, machine-recognized by Tesseract, mean confidence 81%:]
Revenue 2026 ($M)
Q1 Q2 Q3 Q4
[End of OCR text from asset-003]
```

The asset id leads to the image and its record in `assets.json`, which holds
the same text with its exact confidence. The confidence runs from 0 to 100
and is a mean: over the words Tesseract read, or over the lines RapidOCR read.
An engine that reports none is written as "no confidence reported". Text at
any confidence is kept, so a consumer that wants only confident text filters
on the figure in the opening line or in `assets.json`. In `content.json` the
same text is a picture annotation with provenance `webshot-ocr`, unmarked.

## `content.json`

`content.json` is a **lossless `DoclingDocument`**, whose schema is owned and
versioned by [docling-core](https://github.com/docling-project/docling-core);
the manifest records the version that produced it. WebShot deliberately
publishes no schema of its own for this file — duplicating an upstream contract
is how the two drift apart. What WebShot guarantees, and tests, is that the file
loads in docling-core.

Everything docling's document model does not represent is carried beside it
rather than dropped:

| In `assets.json` | Why it is not in `content.json` |
|---|---|
| `visual_assets` | images, canvases, SVGs, and iframes with their recognized text, confidence, engine, and SHA-256, including those inside open shadow roots and frames the page can script, in reading order. An iframe's `frame_content` says whether its document was read into `content.html` (`dom`, so its recognized text repeats what the bundle already has) or its screenshot is all the bundle holds of it (`screenshot`, in which case the screenshot stands in `content.html` as an image and its recognized text is attached to that picture). `null` on every other kind |
| `form_fields` | docling models no form state; password values arrive already redacted |
| `embedded_media` | audio and video with their caption/subtitle tracks |

Definition lists are *not* here — docling models them natively as list groups.

## `manifest.json`

The manifest is the provenance record and the checksum table:

```json
{
  "bundle_format": 3,
  "generator": "WebShot",
  "generator_version": "3.0.0",
  "captured_at": "2026-08-20T17:14:02.481Z",
  "source": "https://example.com/article",
  "final_url": "https://example.com/article",
  "title": "…",
  "page_metadata":  { "title": "…", "language": "en", "canonicalUrl": "…", … },
  "discovered_metadata": { "title": "…", "author": "…", "date": "", "sitename": "…" },
  "content_discovery": { "strategy": "trafilatura", "selector": "main" },
  "auth_mode": "none",
  "source_response": { "http_status": 200, "content_type": "text/html" },
  "pdf": { "file": "../article.pdf", "pages": 2, "bytes": …, "sha256": "…",
           "tagged": true, "bookmarks": true },
  "content": { "items": 21, "tables": 1, "links": 0, "chunks": 11,
               "text_characters": 1754, "visual_assets": 1, "ocr_words": 3 },
  "ocr": { "enabled": true, "language": "eng",
           "page_segmentation_mode": 11, "engine": "Tesseract" },
  "tool_versions": { "webshot": "…", "playwright": "…", "chromium": "…",
                     "docling_slim": "…", "tesseract": "…", … },
  "files": { "content.md": { "bytes": …, "sha256": "…" }, … },
  "warnings": [],
  "ocr_notice": "…"
}
```

Four fields are worth knowing about:

- **`final_url`** is where the browser actually ended up. It differs from
  `source` when the page redirected, and that difference is often the whole
  story.
- **`content_discovery`** records *which* strategy chose the captured region —
  `explicit`, `trafilatura`, `static-selectors`, or `full-page` — so a
  surprising capture can be explained rather than guessed at.
- **`page_metadata`** is what the DOM declared; **`discovered_metadata`** is what
  Trafilatura read. They are kept apart so a consumer can tell where a value came
  from, and an empty string means the page declared nothing — WebShot does not
  infer.
- **`ocr.engine`** is the engine that actually recognized something, not the one
  that was requested. A capture with nothing to recognize names no engine.

`files` lists every published file with its SHA-256 — every file except
`manifest.json` itself, which cannot checksum itself. The QA report carries that
one, so a consumer holding both can verify the whole bundle.

## Warnings are part of the contract

`warnings` is where degradation surfaces: OCR unavailable, a language pack
missing, the asset cap reached, the lazy-load scroll limit hit, requests for
the page's resources that failed. Silent
degradation is treated as a bug. If a stage did less than you asked for, the
manifest says so, and so does the QA report.

## Schemas

Published JSON Schemas ship with the package and in `schemas/`:

```bash
webshot schema                 # list them
webshot schema manifest        # print one
webshot schema chunk
webshot schema qa-report
webshot schema --write ./out   # write them all
```

Additive changes are minor; breaking ones bump `bundle_format`. CI fails if a
published schema and its model have drifted, and fails if a recorded golden
bundle stops validating against the contract.

## Reading a bundle safely

A bundle describes a page someone else wrote. Treat its contents as data:
`content.md`, chunk text, asset alt text, and page metadata are all
attacker-influenced on an untrusted source. If an agent is the consumer, the
[MCP guide](mcp.md) states the rule the client must apply.

Verify before you trust: check `pdf.sha256` and the `files` table. There is a
detectable crash window between the bundle's rename and the PDF's — the manifest
hash will not match — which is exactly why consumers are asked to verify
checksums rather than assume atomicity.

## Sharing a bundle: it carries provider URLs, tokens included

`assets.json` and each video's `steps.json` record every asset's `source_url` as
the provider gave it. For some providers that URL carries an access token —
Guidde's media sit on `storage.app.guidde.com` behind `?alt=media&token=<uuid>`,
and a single LMS journey capture was measured holding **3,453 such URLs across
156 files**.

**These are the provider's tokens, not yours.** The §6 invariant is intact:
nothing in a bundle carries the capturing user's session, cookies or password
values, and these URLs reach none of the surfaces a reader or an agent uses —
not `content.md`, `content.txt`, `chunks.jsonl`, `manifest.json`, nor a journey
corpus's `index.json`.

They are kept deliberately. An asset's `source_url` is how a consumer verifies
that a file in the bundle came from where the bundle says it did, which is the
auditability docs/11 principle 4 exists to provide; a stripped URL would leave
the provenance record unable to do its job.

But they are bearer-shaped, so whoever holds the bundle can fetch those objects
for as long as the provider honours the token. That is one of the concrete things
[the capture-ethics policy](../11-capture-ethics.md) means when it says a bundle
at rest is as sensitive as the content it came from. Handle a bundle you intend
to share the way you would handle the source material, and if that is not
acceptable for a given provider, the answer is not to share that bundle rather
than to edit the provenance out of it.

## Migrating from format 2

`--legacy-bundle` additionally emits v2-format `content.json` and
`chunks.jsonl` under `legacy/`, listed in the manifest with `legacy: true`. It
ships in v3.0 and is removed in v3.1. The field-by-field mapping is in the
[migration guide](../migration-v2-to-v3.md).
