"""The video appendix: every embedded walkthrough, printed after the page.

A captured page whose procedure lives inside a player prints as a dead
rectangle.  This appendix is what makes the PDF stand on its own — the same
walkthrough the bundle carries, rendered as a document, with a bookmark per
video and per step so a reader can jump straight to "step 3 of the second
video" without scrolling.

It is the web-path counterpart of the protected path's transcript appendix and
deliberately the same mechanism: authored as HTML so Chromium does the line
breaking, the pagination and the link rectangles; appended with pypdf; the
back-links rewritten into internal jumps by `render/appendix.py`, which both
paths share.

**The appended pages are not tagged.**  pypdf's merge does not carry a
structure tree across (docs/09 S8).  The capture's own pages keep the structure
Chromium built for them; the appendix pages have none, and the manifest records
`appendix_tagged: false` rather than letting `tagged: true` be read as covering
the whole file.

Step screenshots are referenced as `file:` URLs into the staging bundle, so the
render is offline — the images were downloaded during capture and nothing here
reaches the network.

**That last sentence is load-bearing, not decorative.**  This prints through a
second, isolated Chromium which carries none of the capture context's request
gating, so a remote URL reaching the authored HTML would be fetched with no
policy applied at all.  None can: every value taken from the playbook is
written as escaped *text*, the only `src` is a resolved path under the staging
directory, and the only `href` is the back-link marker scheme.  Anything added
here that emits a URL from playbook data has to keep that true.
"""

from __future__ import annotations

import html
import shutil
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from ..bundle.videos import (
    SILENT_STEP_NOTE,
    VideoBundle,
    timestamp,
)
from .appendix import (
    BACK_LINK_SCHEME,
    assert_no_marker_links,
    marker_links,
    rewrite_back_links,
)
from .pdf import render_document_to_pdf

#: Where a video section's back-link points.  The capture's own content starts
#: at page 1, which is where a reader who followed a walkthrough came from.
CAPTURE_FIRST_PAGE = 1

GUIDE = (
    "The pages before this appendix are the captured page exactly as it rendered.",
    "This appendix is the written walkthrough of every video embedded in that "
    "page: the instruction for each step, the narration on the video's own "
    "clock, and the annotated screenshot for that moment.",
    "It is the complete narration plus the per-step instructions, not a "
    "transcript of a recording. Steps with no narration are silent screen "
    "action and are marked as such.",
    "The same walkthroughs are in the machine-readable bundle as "
    "videos/<id>/walkthrough.md, steps.json, transcript.txt and "
    "transcript.vtt, with per-step retrieval chunks in chunks.jsonl.",
    "These appendix pages carry no PDF structure tags: merging an appendix "
    "does not preserve one. The captured pages before them are unaffected.",
)

STYLE = """
html { -webkit-print-color-adjust: exact; }
body { font: 10pt/1.45 "Helvetica Neue", Helvetica, Arial, sans-serif; color: #1d2738; }
h1 { font-size: 19pt; color: #172033; margin: 0 0 6pt; }
h2 { font-size: 15pt; color: #172033; margin: 0 0 3pt; }
h3 { font-size: 11.5pt; color: #172033; margin: 14pt 0 3pt; break-after: avoid; }
p.subtitle { font-size: 11.5pt; font-weight: bold; color: #172033; margin: 0 0 12pt; }
ul.guide { font-size: 9.5pt; color: #27324a; padding-left: 14pt; margin: 0 0 16pt; }
ul.guide li { margin-bottom: 6pt; }
p.provenance { font-size: 8.5pt; font-style: italic; color: #5b6578;
               border-top: 1px solid #c6ccd8; padding-top: 8pt; margin-top: 20pt; }
section.video { break-before: page; }
p.meta { font-size: 8.75pt; color: #5b6578; margin: 0 0 8pt; }
p.backlink { font-size: 8.5pt; font-weight: bold; margin: 0 0 14pt; }
p.backlink a { color: #2457a6; text-decoration: none; }
h3 a.steplink { color: inherit; text-decoration: none; }
div.step { break-inside: avoid; margin: 0 0 12pt; }
p.instruction { margin: 0 0 6pt; }
p.silent { margin: 0 0 6pt; font-style: italic; color: #5b6578; }
img.shot { display: block; max-width: 100%; max-height: 3.6in; width: auto;
           border: 1px solid #c6ccd8; border-radius: 3px; margin: 0 0 6pt; }
ul.narration { font-size: 9pt; color: #27324a; padding-left: 14pt; margin: 0; }
ul.narration li { margin-bottom: 2pt; }
span.at { color: #5b6578; font-variant-numeric: tabular-nums; }
"""


@dataclass(slots=True)
class ComposedAppendix:
    """What the merge produced, for the manifest and for the checks."""

    capture_pages: int
    total_pages: int
    videos: int = 0
    steps: int = 0

    @property
    def appendix_pages(self) -> int:
        return self.total_pages - self.capture_pages


def _escape(value: str) -> str:
    return html.escape(value or "")


def _step_html(bundle: VideoBundle, staging: Path) -> list[str]:
    parts: list[str] = []
    for step in bundle.playbook.steps:
        heading = step.title or f"Step {step.number}"
        # The step number is itself the back-link, which does two jobs: a
        # reader can return to the captured page from any step, and the merge
        # reads each step's *exact* page off its own annotation instead of
        # estimating it from where the section began.
        parts += [
            '<div class="step">',
            f'<h3><a class="steplink" href="{BACK_LINK_SCHEME}{CAPTURE_FIRST_PAGE}">'
            f"Step {step.number}</a> — {_escape(heading)} "
            f'<span class="at">· {timestamp(step.start)}</span></h3>',
        ]
        if step.silent:
            parts.append(f'<p class="silent">{_escape(SILENT_STEP_NOTE)}</p>')
        elif step.instruction:
            parts.append(f'<p class="instruction">{_escape(step.instruction)}</p>')
        asset = next(
            (
                candidate
                for candidate in bundle.assets
                if candidate.kind == "step-screenshot" and candidate.step == step.number
            ),
            None,
        )
        if asset:
            source = (staging / asset.file).resolve().as_uri()
            parts.append(
                f'<img class="shot" src="{_escape(source)}" '
                f'alt="{_escape(step.instruction or heading)}">'
            )
        if step.cues:
            parts.append('<ul class="narration">')
            parts += [
                f'<li><span class="at">{timestamp(cue.start)}</span> '
                f"{_escape(cue.text)}</li>"
                for cue in step.cues
            ]
            parts.append("</ul>")
        parts.append("</div>")
    return parts


def appendix_html(
    bundles: Sequence[VideoBundle],
    staging: Path,
    *,
    title: str,
    generator_version: str,
    paper_format: str,
    margin: str,
) -> str:
    """Author the appendix for `bundles`, one section per video.

    The page box is the capture's own, so the appendix prints on the same paper
    as the pages it is appended to rather than reverting to a fixed Letter.
    """
    page_rule = f"@page {{ size: {paper_format}; margin: {margin}; }}"
    total_steps = sum(len(bundle.playbook.steps) for bundle in bundles)
    parts = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        f"<title>{_escape(title)} — embedded video walkthroughs</title>",
        f"<style>{page_rule}{STYLE}</style></head><body>",
        "<h1>Embedded Video Walkthroughs</h1>",
        f'<p class="subtitle">{_escape(title)}</p>',
        '<ul class="guide">',
        *(f"<li>{_escape(item)}</li>" for item in GUIDE),
        "</ul>",
        f'<p class="provenance">Generated by WebShot {_escape(generator_version)}'
        f" — {len(bundles)} video(s), {total_steps} step(s)</p>",
    ]
    for bundle in bundles:
        playbook = bundle.playbook
        meta = [f"{len(playbook.steps)} steps", timestamp(playbook.duration)]
        if playbook.organization:
            meta.append(_escape(playbook.organization))
        if playbook.last_updated_by:
            meta.append(f"last updated by {_escape(playbook.last_updated_by)}")
        parts += [
            f'<section class="video" id="video-{_escape(playbook.id)}">',
            f"<h2>{_escape(playbook.title)}</h2>",
            f'<p class="meta">{" · ".join(meta)} · {_escape(playbook.source_url)}</p>',
            f'<p class="backlink"><a href="{BACK_LINK_SCHEME}{CAPTURE_FIRST_PAGE}">'
            "Return to the captured page</a></p>",
            *_step_html(bundle, staging),
            "</section>",
        ]
    parts.append("</body></html>")
    return "\n".join(parts)


def _section_and_step_pages(
    writer: PdfWriter, capture_pages: int, bundles: Sequence[VideoBundle]
) -> list[tuple[int, list[int]]]:
    """The merged-document page each section and each step actually starts on.

    Read off the appendix's own back-link annotations rather than estimated:
    the authoring puts exactly one marker on a video's heading and one on each
    of its step headings, in that order, so walking the markers in page order
    gives every position exactly.  A step bookmark that lands a page away from
    its step is the kind of small wrongness that makes an outline untrusted.

    Falls back to the section's own page for any step whose marker is missing,
    which can only happen if the authoring and this walk disagree — better a
    bookmark that points at the right video than none at all.
    """
    markers = [
        index
        for index, _annotation, _number in marker_links(writer.pages)
        if index >= capture_pages
    ]
    positions: list[tuple[int, list[int]]] = []
    cursor = 0
    for bundle in bundles:
        expected = 1 + len(bundle.playbook.steps)
        found = markers[cursor : cursor + expected]
        cursor += len(found)
        start = found[0] if found else capture_pages
        step_pages = list(found[1:])
        step_pages += [start] * (len(bundle.playbook.steps) - len(step_pages))
        positions.append((start, step_pages))
    return positions


def compose(
    capture_pdf: Path,
    appendix_pdf: Path,
    destination: Path,
    *,
    bundles: Sequence[VideoBundle],
    outline: bool = True,
) -> ComposedAppendix:
    """Append the rendered appendix and build the bookmarks.

    The writer is cloned from the capture so Chromium's structure tree, its
    heading outline, and the document metadata survive; the appendix's own
    outline is deliberately not imported, because the bookmarks built here are
    the navigable ones.

    `outline=False` is `--no-outline`, and it was ignored here: the walkthrough
    root, its per-video children and every step bookmark were added
    unconditionally, so a capture that asked for no bookmarks got a whole tree
    of them — and `manifest.pdf.bookmarks` reported `false` while the file
    disagreed (docs/09 P8-30). The back-link rewrite is not part of that: it
    repairs links the appendix HTML authored, which have nothing to do with the
    document outline.
    """
    writer = PdfWriter(clone_from=str(capture_pdf))
    # Before the append: this is the capture's own page count, and reading it
    # here saves the caller a second full parse of the same file.
    capture_pages = len(writer.pages)
    writer.append(str(appendix_pdf), import_outline=False)
    total_pages = len(writer.pages)

    # Read every position before the rewrite, which replaces the annotations
    # the positions are read from.
    positions = _section_and_step_pages(writer, capture_pages, bundles)
    rewrite_back_links(
        writer,
        capture_pages,
        label="captured page",
        first_appendix_page=capture_pages,
    )

    steps_total = sum(len(bundle.playbook.steps) for bundle in bundles)
    if outline:
        walkthroughs = writer.add_outline_item(
            "Embedded video walkthroughs", capture_pages
        )
        for bundle, (start, step_pages) in zip(bundles, positions, strict=True):
            parent = writer.add_outline_item(
                bundle.playbook.title, start, parent=walkthroughs
            )
            for step, page in zip(bundle.playbook.steps, step_pages, strict=True):
                writer.add_outline_item(
                    f"Step {step.number} — {step.title or 'step'} "
                    f"({timestamp(step.start)})",
                    page,
                    parent=parent,
                )
    with destination.open("wb") as handle:
        writer.write(handle)
    return ComposedAppendix(
        capture_pages=capture_pages,
        total_pages=total_pages,
        videos=len(bundles),
        steps=steps_total,
    )


def append_video_appendix(
    capture_pdf: Path,
    staging: Path,
    bundles: Sequence[VideoBundle],
    *,
    title: str,
    generator_version: str,
    paper_format: str,
    margin: str,
    landscape: bool = False,
    prefer_css_page_size: bool = False,
    outline: bool = True,
) -> ComposedAppendix:
    """Render the appendix and merge it into `capture_pdf`, in place.

    Everything intermediate — the authored HTML, the printed appendix, the
    merged file — lives in a temporary directory that goes away whatever
    happens.  It cannot be staged beside the output: the appendix PDF is as
    large as every step screenshot in it, and leaving one of those in the
    user's output directory on every capture is not a tidiness problem, it is
    megabytes of litter they did not ask for.

    `capture_pages` is measured here rather than passed in, because it is
    exactly `len(writer.pages)` before the append and the caller would only be
    re-parsing the same file to learn it.
    """
    work = Path(tempfile.mkdtemp(prefix="webshot-video-appendix-"))
    try:
        document = work / "video-appendix.html"
        document.write_text(
            appendix_html(
                bundles,
                staging,
                title=title,
                generator_version=generator_version,
                paper_format=paper_format,
                margin=margin,
            ),
            encoding="utf-8",
            newline="\n",
        )
        appendix_pdf = work / "video-appendix.pdf"
        render_document_to_pdf(
            document,
            appendix_pdf,
            paper_format=paper_format,
            margin=margin,
            landscape=landscape,
            prefer_css_page_size=prefer_css_page_size,
            outline=outline,
        )
        merged = work / "merged.pdf"
        composed = compose(
            capture_pdf, appendix_pdf, merged, bundles=bundles, outline=outline
        )
        assert_no_marker_links(
            PdfReader(str(merged)), first_appendix_page=composed.capture_pages
        )
        # Renamed over the target only after every check has passed, the way
        # `render/embed.py` stages its rewrite, so an interrupted merge leaves
        # the rendered capture intact rather than truncated.
        shutil.move(str(merged), str(capture_pdf))
        return composed
    finally:
        shutil.rmtree(work, ignore_errors=True)
