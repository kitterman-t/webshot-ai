# Signed-in pages and protected documents

This page covers content you can see only after signing in: an ordinary page
behind a login, and a document shown only inside an authenticated viewer. Use
these options for material you are allowed to view and keep a copy of. The
site's terms and your organization's policies still apply, and the
[capture ethics policy](../11-capture-ethics.md) sets out what WebShot will not
be made to do.

## A storage-state file

```bash
webshot "https://app.example.com/report" --storage-state auth.json
```

A Playwright storage-state file holds cookies that work as a login, so treat it
like a password. WebShot never copies it into the bundle or the PDF, and the
values of password fields on a captured page are redacted from the bundle's
semantic files ([security model](security.md#credentials)).

## A private browser profile

For a site that needs interactive sign-in or MFA, such as Microsoft 365, use a
private browser profile instead. The first run opens a headed browser and
pauses while you sign in:

```bash
webshot "https://tenant.sharepoint.com/path/to/document" \
  --auth-profile .webshot-auth/work \
  --interactive-auth \
  --protected-viewer \
  --output output/pdf/protected-document.pdf
```

Once the profile is signed in, later runs reuse it headless, with no cookies
exported to a file:

```bash
webshot "https://tenant.sharepoint.com/path/to/document" \
  --auth-profile .webshot-auth/work \
  --protected-viewer \
  --output output/pdf/protected-document.pdf
```

WebShot creates profiles with owner-only permissions, makes an existing one
owner-only before using it, and refuses one it cannot. Keep profiles outside
version control, never share them, and use a separate profile for each trust
boundary, such as each organization you sign in to.

## Protected PDF viewers

`--protected-viewer` captures a document that is shown only inside an
authenticated viewer, such as SharePoint's PDF viewer. It works from the pages
the viewer has already rendered for you: it never requests the native file and
never reads browser credentials. It does make a copy of what you can see, the
way printing each page would, so if the document's owner has blocked
downloading or copying, treat that block as covering WebShot too. WebShot does
not detect such a block for you.

### What the PDF holds

On this path the PDF is the main, self-contained artifact. It holds:

- every source page, at the quality it was captured
- an invisible OCR text layer positioned on each source page
- a visible, page-addressable transcript appendix after the source pages
- bookmarks that separate the original document from the transcript and link
  each transcript section back to its source page
- embedded associated files with the text as JSON and plain text, retrieval
  chunks, capture provenance, OCR confidence and word coordinates
- document metadata giving the source-page count and naming the embedded data

A vision model can read the original pages, an ordinary PDF parser can read
the visible transcript, and a tool that understands associated files can read
the structured data, all from the same file. The PDF's page count is the
source-page count plus the appendix pages. The viewer's page count and the
sequence of captured page images must match before anything is published; a
mismatch fails the run with exit 5 rather than producing a shorter document.

The appendix is rendered from HTML, but appending it to image pages does not
carry a structure tree across. The composed PDF is searchable and bookmarked,
and it is **not** a tagged PDF; the manifest says so rather than claiming
otherwise.

Text recognition on this path always uses Tesseract, because the PDF's text
layer and its word coordinates come from OCRmyPDF. `--ocr-engine rapid` is
warned and ignored here.

### The bundle directory

The `.ai` directory beside the PDF is for diagnostics and development; you do
not need it to share the capture. It holds:

- lossless page images in `pages/page-NNN.png`
- word positions and confidence in `ocr/page-NNN.tsv`
- `ocr/text-layer.txt`, the plain text of the PDF's own searchable layer
- page-addressable `content.json`, `content.md` and `content.txt`
- page-scoped retrieval chunks in `chunks.jsonl`
- a manifest recording page counts, geometry, authentication mode, checksums,
  and that no native file was downloaded

[Bundle format 3](bundle-format.md) describes the files in detail.

## Archival output and conformance checking

```bash
webshot "https://tenant.sharepoint.com/.../Doc.pdf" \
  --protected-viewer \
  --auth-profile ~/.webshot/profiles/work \
  --pdfa --validate-pdf
```

`--pdfa` converts the protected-path PDF to **PDF/A-3**, the level that permits
the embedded associated files above (the PDF/A-2b default forbids them). It is
available on protected-viewer captures only: converting a web capture would
strip the accessibility tags Chromium builds, which is a worse trade than not
offering it ([ADR-0010](../adr/0010-validation-gates.md)).

`--validate-pdf` checks the published file with
[veraPDF](https://verapdf.org/), from a local `verapdf` binary or the
`verapdf/cli` container. It validates the final composed file, after the OCR
layer, the appendix, the bookmarks and the embedded files, because every one of
those steps happens after the conversion. The results go into the `--report`
file and into `<output>.verapdf.json` next to the PDF. If no validator is
available, WebShot reports that conformance was not established rather than
implying it passed; `--validate-pdf=strict` exits 7 when a file that *was*
checked is not conformant. Run `webshot doctor` to see whether this machine can
validate.
