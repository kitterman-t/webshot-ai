# Quickstart

## Install

WebShot is not published to PyPI, and `pip install webshot` installs an
unrelated package that happens to share the name. Install from a source
checkout instead, with [uv](https://docs.astral.sh/uv/) and Python 3.11 or
newer:

```bash
git clone https://github.com/kitterman-t/webshot-ai && cd webshot-ai
uv sync
uv run playwright install chromium
```

That puts the `webshot` command in the checkout's `.venv`. The examples on
these pages type `webshot` directly, as they would after
`source .venv/bin/activate`; `uv run webshot` works without activating.

Then check the machine:

```bash
webshot doctor
```

`doctor` reports every dependency with a pass/optional/warn/fail and a one-line
fix, and exits `0` when everything required is present. Tesseract is optional but
recommended — without it, captures still work and text recognition degrades to a
manifest warning, which is exactly what `doctor` will tell you. To install it:

```bash
brew install tesseract                # macOS
sudo apt-get install tesseract-ocr    # Ubuntu or Debian
```

Add the language packs you need, then choose them per run with
`--ocr-language eng`, `--ocr-language spa`, or a combination such as
`--ocr-language eng+spa`; `webshot doctor --ocr-language eng+spa` checks that
they are installed. Where a Tesseract binary is not an option, the `ocr-rapid`
extra below adds RapidOCR, chosen per run with `--ocr-engine rapid`. It runs on
ONNX Runtime and ships its models inside the wheel, so it needs no system
package and no download.

Extras, each one optional, installed with `uv sync --extra NAME`. Name every
extra you want in one command (`uv sync --extra office --extra mcp`), because
`uv sync` removes the ones you leave out:

| Extra | What it adds |
|---|---|
| `office` | DOCX, PPTX, and XLSX inputs |
| `ocr-rapid` | RapidOCR, a pip-only recognizer for machines without a Tesseract binary |
| `mcp` | the `webshot mcp` server |
| `chunk-hybrid` | docling's tokenizer-aware `HybridChunker`, for re-chunking downstream |
| `pdfa` | kept as an alias; PDF/A support is in the base install |

## Capture a page

```bash
webshot https://example.com
```

That writes two things:

```
output/pdf/example.com.pdf     # the readable, tagged PDF
output/pdf/example.com.ai/     # the AI-ready bundle
```

The examples below use `https://example.com/article` to stand for any article
page; put the address of a real one in its place.

The bundle is [format 3](bundle-format.md). The two files most consumers start
with are `content.md` (the whole document as markdown) and `chunks.jsonl` (one
retrieval-sized chunk per line, each carrying provenance back into
`content.json`).

`--output` names the PDF, and the bundle lands beside it with an `.ai` suffix
unless `--ai-bundle-dir` says otherwise. `--no-ai-bundle` writes the PDF only,
`--no-ocr` skips text recognition, and `--max-assets` caps how many visuals are
kept (50 by default). If a capture is no use to you without recognized text,
`--require-ocr` turns a missing OCR engine into exit 6 instead of a manifest
warning, checked before the browser opens.

## Capture only the article

```bash
webshot https://example.com/article --auto-selector
```

`--auto-selector` asks Trafilatura which region of the page is the article and
falls back to a static selector list if it cannot tell. The manifest records
which strategy won, so a surprising capture can be explained. To choose the
region yourself:

```bash
webshot https://example.com/article --selector "main.article" --exclude ".newsletter-signup"
```

## Faithful vs clean

`--mode clean` (the default) hides cookie banners and common page chrome and
records how many elements it hid. `--mode faithful` hides nothing, which suits
dashboards and pages whose layout is the point:

```bash
webshot "https://example.com/dashboard" \
  --mode faithful \
  --media screen \
  --landscape \
  --wait-for "[data-dashboard-ready='true']"
```

Capture is an act of record, so what was altered is declared either way: the
manifest's warnings and the hidden-element count are part of the bundle.

## Local documents

A local path works wherever a URL does:

```bash
webshot ./report.md
webshot ./data.csv
webshot ./slides.pptx        # needs the office extra
```

Markdown, JSON, JSONL, CSV, TSV, plain text, XML, YAML, SVG, and images work
from the base install. DOCX, PPTX, and XLSX need the `office` extra; EPUB and
ZIP work without it. Inputs above 100 MB are refused, all XML is parsed through
`defusedxml`, and archives are checked against expansion, member-count, and
zip-slip limits before anything is extracted.

## A protected document

For a document that only exists inside an authenticated viewer:

```bash
# once, in a terminal — a headed browser opens and waits for you to sign in
webshot --interactive-auth --auth-profile ~/.webshot/profiles/work https://…

# afterwards, headless
webshot https://…/doc.pdf --protected-viewer --auth-profile ~/.webshot/profiles/work
```

WebShot captures the pages the viewer already rendered for you, packs them
losslessly, and adds an invisible OCR text layer so the result is searchable. It
never requests the native file. The page-count invariant — captured pages plus
appendix pages equal the PDF's pages — is checked before publication, and a
mismatch is a failure rather than a shorter document.
[Signed-in pages and protected documents](signed-in.md) covers storage-state
files, browser profiles, what the protected PDF holds, and archival PDF/A
output.

## Check what you got

```bash
webshot https://example.com/article --report report.json
```

The QA report is schema v2: the options as they resolved, wall clock per
pipeline stage, the counts, every warning, which validation gates ran and what
they said, and the SHA-256 of the bundle's manifest. `webshot schema qa-report`
prints the schema it conforms to.

## Next

- [CLI reference](cli.md) for every option.
- [Configuration](configuration.md) to stop repeating them.
- [Signed-in pages and protected documents](signed-in.md) for content behind a
  login.
- [MCP server](mcp.md) if the consumer is an agent rather than a person.
