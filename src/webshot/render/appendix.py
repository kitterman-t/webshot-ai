"""The appendix mechanism both paths share: back-link markers and the merge.

WebShot appends two kinds of appendix to a PDF — the protected path's OCR
transcript, and the web path's walkthroughs of the videos embedded in the page.
What is here is what they genuinely have in common: the marker scheme and the
pypdf rewrite around it, which is the part that is fiddly enough to be worth
having exactly one of.

What is deliberately *not* here is each appendix's own document — their guide
text says different things, and their two compositions differ in ways that are
not incidental (the protected path clones an OCRmyPDF catalog to keep its PDF/A
identification, and builds a flat page list where the web path builds a
video → step tree).  Their HTML scaffolding is still two near-identical copies;
factoring it would risk re-paginating the protected appendix for a cosmetic
win, so it stays, said out loud rather than left to be discovered.

*Sections start on a new page.*  Every appendix section begins a page, so "the
page where section N starts" is well defined and can carry a bookmark.

*Back-links are authored as external URIs.*  Chromium turns `<a href>` into a
real link annotation at exactly the right rectangle, which hand-computed
geometry never gets right.  The URI is a marker in a reserved, unroutable
domain (RFC 2606 `.invalid`); `rewrite_back_links` turns every one of them into
an internal jump, and `assert_no_marker_links` refuses to publish a file where
one survived — so no outbound link WebShot invented ever reaches a deliverable.

*The merge does not carry a structure tree.*  Appending a Chromium-tagged
appendix drops its `StructTreeRoot` (docs/09 S8), verified in both merge
directions.  An appendix is authored from tagged HTML for rendering quality
only, and nothing that uses this module may claim the appendix pages are
tagged.  Where the base document was tagged its own pages stay tagged; the
appended ones are not, and the manifest says so.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, NameObject

from ..errors import CaptureIntegrityError

#: The marker scheme an authored appendix uses for "jump back to page N".
BACK_LINK_SCHEME = "https://webshot.invalid/source-page/"


def back_link_target(uri: str) -> int | None:
    """The page a back-link URI points at, or None if it is not one."""
    if not uri.startswith(BACK_LINK_SCHEME):
        return None
    tail = uri[len(BACK_LINK_SCHEME) :]
    return int(tail) if tail.isdigit() else None


def marker_links(
    pages: Iterable[Any],
) -> Iterator[tuple[int, DictionaryObject, int]]:
    """Every back-link marker in `pages`, as (page index, annotation, target).

    Shared by the rewrite and the assertion that follows it, so the check
    cannot stop covering what the rewrite does.
    """
    for index, page in enumerate(pages):
        for reference in page.get("/Annots") or []:
            annotation = reference.get_object()
            action = annotation.get("/A")
            if not action:
                continue
            number = back_link_target(str(action.get_object().get("/URI") or ""))
            if number is not None:
                yield index, annotation, number


def rewrite_back_links(
    writer: PdfWriter,
    target_pages: int,
    *,
    label: str = "page",
    first_appendix_page: int = 0,
) -> dict[int, int]:
    """Turn the appendix's marker URIs into jumps to the document's own pages.

    Chromium placed the link rectangle from the rendered text; only the
    destination changes here.  Returns the merged-document page index each
    section starts on, which is what the bookmarks point at.

    `label` is what the pages are called in a failure message: the protected
    path links back to a "source page", the web path to a page of the capture.

    `first_appendix_page` is where WebShot's own authored pages begin, and
    every page before it is left alone. On the protected path the base pages
    are OCR'd images and can carry no link, so this defaulted to zero
    harmlessly — but a *captured web page* can contain any `<a href>`,
    including one in this marker's own scheme. Rewriting those would turn a
    reader's outbound link into an internal jump, and one pointing past the
    page count would fail a capture whose PDF was already built
    (docs/09 P7-10).
    """
    section_start: dict[int, int] = {}
    for index, annotation, number in marker_links(writer.pages):
        if index < first_appendix_page:
            continue
        # Against the target-page count, not the merged one: a link to "page 7"
        # of a 2-page document must fail, not silently land on the appendix's
        # fifth page.
        if not 1 <= number <= target_pages:
            raise CaptureIntegrityError(
                f"An appendix back-link points at {label} {number}, which the "
                f"composed PDF does not have ({target_pages} {label}(s))."
            )
        annotation[NameObject("/A")] = DictionaryObject(
            {
                NameObject("/S"): NameObject("/GoTo"),
                NameObject("/D"): ArrayObject(
                    [writer.pages[number - 1].indirect_reference, NameObject("/Fit")]
                ),
            }
        )
        section_start.setdefault(number, index)
    return section_start


def assert_no_marker_links(reader: PdfReader, *, first_appendix_page: int = 0) -> None:
    """A published capture must not carry an outbound link WebShot invented.

    Bounded the same way the rewrite is, and for the same reason: a marker on a
    *captured* page is not one WebShot invented, it is the page's own `<a href>`
    reproduced faithfully. Leaving it alone is what capture means — and having
    the rewrite skip it while this still raised on it turned a silent hijack
    into a hard failure of an already-rendered capture (docs/09 P7-12).
    """
    for index, _annotation, _number in marker_links(reader.pages):
        if index < first_appendix_page:
            continue
        raise CaptureIntegrityError(
            f"An unresolved appendix back-link survived on page {index + 1}."
        )
