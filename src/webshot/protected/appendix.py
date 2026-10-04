"""Author the transcript appendix as HTML, so the RENDER stage can print it.

v2 drew this appendix by hand with reportlab: line wrapping, page breaks, and
link rectangles were all arithmetic.  Chromium already does all three, so the
appendix is now a document rather than a drawing — which is what deletes the
reportlab dependency (docs/05 task 1.3).

Two conventions make the merged PDF navigable after the appendix has been
appended to the page images:

*Sections start on a new page.*  Every source page's transcript begins a page,
so "the appendix page where source page N is transcribed" is well defined and
can carry a bookmark.

*Back-links are authored as external URIs.*  Chromium turns `<a href>` into a
real link annotation at exactly the right rectangle, which hand-computed
geometry never gets right.  The URI is a marker in a reserved, unroutable
domain (RFC 2606 `.invalid`); `render/appendix.py` — shared with the web path's
video appendix — rewrites every one of them into an internal jump to the source
page and fails if any survives, so no outbound link ever reaches the published
PDF.

The appendix is authored from tagged HTML for rendering quality only.  pypdf's
merge does not carry a structure tree across (docs/09 S8), so the composed file
is **not** a tagged PDF and nothing here claims it is.
"""

from __future__ import annotations

import html
from collections.abc import Sequence
from typing import Protocol

from ..render.appendix import BACK_LINK_SCHEME, back_link_target

__all__ = [
    "BACK_LINK_SCHEME",
    "PAGE_FORMAT",
    "PAGE_MARGIN",
    "appendix_html",
    "back_link_target",
]

#: The page box the appendix is written for. The RENDER stage is told the same
#: values, because Chromium's print parameters win over CSS `@page`; keeping one
#: definition is what stops the two drifting apart.
PAGE_FORMAT = "Letter"
PAGE_MARGIN = "0.6in"

GUIDE = (
    "This PDF is self-contained and optimized for both visual and text-based AI systems.",
    "The original source pages appear first and preserve all visible text, images, tables, charts, and layout.",
    "Each original page has a positioned invisible OCR layer for search, selection, citation, and page-local extraction.",
    "The appendix that follows repeats OCR text in a simple visible reading order for systems that do not process image OCR layers reliably.",
    "Machine-readable JSON, plain text, retrieval chunks, capture provenance, and word coordinates are embedded as associated files inside this PDF.",
    "OCR is probabilistic. For charts, photographs, diagrams, or uncertain text, inspect the corresponding original source page visually.",
    "Appendix transcripts intentionally duplicate the OCR layer. Treat each appendix heading as a reference to its numbered original source page.",
)

STYLE = f"""
@page {{ size: {PAGE_FORMAT}; margin: {PAGE_MARGIN}; }}
html {{ -webkit-print-color-adjust: exact; }}
body {{ font: 9.25pt/1.3 "Helvetica Neue", Helvetica, Arial, sans-serif; color: #1d2738; }}
h1 {{ font-size: 20pt; color: #172033; margin: 0 0 6pt; }}
h2 {{ font-size: 15pt; color: #172033; margin: 0 0 4pt; }}
p.subtitle {{ font-size: 12pt; font-weight: bold; color: #172033; margin: 0 0 14pt; }}
ul.guide {{ font-size: 10.5pt; color: #27324a; padding-left: 14pt; margin: 0 0 18pt; }}
ul.guide li {{ margin-bottom: 7pt; }}
p.provenance {{ font-size: 8.5pt; font-style: italic; color: #5b6578;
               border-top: 1px solid #c6ccd8; padding-top: 8pt; margin-top: 22pt; }}
section {{ break-before: page; }}
p.confidence {{ font-size: 8.5pt; color: #1d2738; margin: 0 0 10pt; }}
p.backlink {{ font-size: 8.5pt; font-weight: bold; margin: 0 0 12pt; }}
p.backlink a {{ color: #2457a6; text-decoration: none; }}
pre.transcript {{ white-space: pre-wrap; overflow-wrap: break-word; font: inherit; margin: 0; }}
"""


class TranscribedPage(Protocol):
    """The subset of an OCR result the appendix reads.

    Deliberately narrow: the complete reading of each page — primary plus any
    alternate — arrives already merged through `page_text`, so this module never
    has to know how the two are combined.
    """

    page: int
    confidence: float | None


def _confidence(value: float | None) -> str:
    return f"{value:.2f}%" if value is not None else "not available"


def appendix_html(
    *,
    title: str,
    generator_version: str,
    pages: Sequence[TranscribedPage],
    page_text: Sequence[str],
) -> str:
    """Render the transcript appendix for `pages`, one section per source page.

    `page_text` is the complete reading for each page in the same order, so the
    caller decides how primary and alternate readings are combined.
    """
    parts = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        f"<title>{html.escape(title)} — AI-readable transcript</title>",
        f"<style>{STYLE}</style></head><body>",
        "<h1>AI-Readable Content Guide</h1>",
        f'<p class="subtitle">{html.escape(title)}</p>',
        '<ul class="guide">',
        *(f"<li>{html.escape(item)}</li>" for item in GUIDE),
        "</ul>",
        f'<p class="provenance">Generated by WebShot {html.escape(generator_version)}'
        f" — source pages: {len(pages)}</p>",
    ]
    for page, text in zip(pages, page_text, strict=True):
        parts += [
            f'<section id="transcript-{page.page}">',
            f"<h2>Machine-Readable Transcript — Source Page {page.page}</h2>",
            f'<p class="confidence">OCR mean confidence: {_confidence(page.confidence)}</p>',
            f'<p class="backlink"><a href="{BACK_LINK_SCHEME}{page.page}">'
            f"Open original visual source page {page.page}</a></p>",
            f'<pre class="transcript">{html.escape(text)}</pre>',
            "</section>",
        ]
    parts.append("</body></html>")
    return "\n".join(parts)
