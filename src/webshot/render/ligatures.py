"""Keep ligature glyphs out of the PDF's text layer, and say so when some remain.

Chromium shapes "fi", "fl", "ff", "ffi" and "ffl" into single ligature glyphs,
and the PDF's `ToUnicode` map records each such glyph as the font's
presentation form — U+FB01 rather than "fi".  Chromium does also write the
letters as `/ActualText`, but pypdf and `pdftotext` read the map, so a search
of the PDF for "first" or "office" misses words the page plainly shows
(docs/09 P16-2).  The bundle is not affected: it is read from the DOM.

So every page WebShot prints is printed with ligatures off.  Three choices in
that are deliberate:

- *`font-variant-ligatures`, never a blanket `font-feature-settings`.* The
  latter is one property: `"liga" 0` in it replaces whatever the page set
  there, tabular numerals and stylistic sets included.
- *Contextual alternates stay on.* Only common, discretionary and historical
  ligatures are turned off; `none` would also turn off `calt`, which scripts
  such as Nastaliq need to render correctly, and which produces no
  presentation form.  Required ligatures (`rlig`) are outside the property.
- *Overrides are measured, not guessed.*  A page's own `font-feature-settings:
  "kern", "liga"` — common typographic boilerplate — re-enables ligatures past
  any `font-variant-ligatures` rule, and so does a more specific `!important`.
  The script finds every element whose computed style still ligates and
  rewrites only the ligature tags on it, inline and `!important`.

The stylesheet is constructed and adopted rather than injected as a `<style>`
element: a constructed sheet is outside a page's `style-src` policy, and it can
be adopted into each open shadow root, which a document stylesheet does not
reach.  What the script cannot reach — a closed shadow root, a frame Playwright
cannot script, a pseudo-element's own override — is why `ligature_forms`
exists: the text layer is checked after printing, and a ligature that got
through is a warning rather than a silent miss.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page as AsyncPage
from pypdf import PdfReader
from pypdf.generic import DictionaryObject, IndirectObject

LOGGER = logging.getLogger("webshot")

#: The computed value Chromium reports for the declaration below, and so the
#: value the script compares against.
LIGATURES_OFF = "no-common-ligatures no-discretionary-ligatures no-historical-ligatures"

#: For documents WebShot writes itself and that no script runs in: the running
#: header and footer templates.
LIGATURES_OFF_DECLARATION = f"font-variant-ligatures:{LIGATURES_OFF};"

#: Returns how many elements had a ligature override of their own rewritten.
SUPPRESS_LIGATURES_SCRIPT = r"""(off) => {
    const LIGATURE_TAGS = new Set(['liga', 'clig', 'dlig', 'hlig']);
    const sheet = new CSSStyleSheet();
    sheet.replaceSync(`*, *::before, *::after { font-variant-ligatures: ${off} !important; }`);
    const roots = [document];
    const collect = root => {
        for (const element of root.querySelectorAll('*')) {
            if (element.shadowRoot) { roots.push(element.shadowRoot); collect(element.shadowRoot); }
        }
    };
    collect(document);
    for (const root of roots) root.adoptedStyleSheets = [...root.adoptedStyleSheets, sheet];
    // `none` also satisfies the rule: it turns off everything `off` does.
    const offTokens = off.split(' ');
    const variantLigates = value => value !== 'none' && !offTokens.every(token => value.includes(token));
    // Chromium serializes `"kern", "liga" 0`; a tag with no value means 1.
    const withoutLigatures = settings => {
        if (!settings || settings === 'normal') return null;
        let changed = false;
        const parts = settings.split(',').map(part => {
            const match = /^\s*"(.{4})"\s*(\S*)\s*$/.exec(part);
            if (!match || !LIGATURE_TAGS.has(match[1])) return part.trim();
            if (match[2] === '0' || match[2] === 'off') return part.trim();
            changed = true;
            return `"${match[1]}" 0`;
        });
        return changed ? parts.join(', ') : null;
    };
    let rewritten = 0;
    for (const root of roots) {
        for (const element of root.querySelectorAll('*')) {
            const style = getComputedStyle(element);
            // An element Chromium gives no computed style at all (`<track>`
            // reports '' for every property) prints nothing, and '' is not a
            // ligature setting. Read as one, it counted every caption track.
            if (style.fontVariantLigatures === '') continue;
            const variant = variantLigates(style.fontVariantLigatures);
            const settings = withoutLigatures(style.fontFeatureSettings);
            if (!variant && settings === null) continue;
            if (variant) element.style.setProperty('font-variant-ligatures', off, 'important');
            if (settings !== null) element.style.setProperty('font-feature-settings', settings, 'important');
            rewritten++;
        }
    }
    return rewritten;
}"""


async def suppress_ligatures(page: AsyncPage) -> int:
    """Turn ligatures off in every frame of the page; count the overrides rewritten.

    Run after every stylesheet is in and the print media is emulated, because
    it measures computed style, and before the bundle is built, so the bundle's
    geometry describes the layout that is printed.  Nothing here reaches the
    bundle's HTML: the snapshot drops `style` attributes and does not
    serialize adopted sheets.

    A frame that cannot be scripted is skipped rather than failed.  Whether
    its text still ligates is a fact about the printed file, and
    `ligature_forms` measures it there.
    """
    rewritten = 0
    for frame in page.frames:
        try:
            rewritten += await frame.evaluate(SUPPRESS_LIGATURES_SCRIPT, LIGATURES_OFF)
        except PlaywrightError as exc:
            LOGGER.debug("Could not turn ligatures off in frame %s: %s", frame.url, exc)
    return rewritten


#: The Latin ligature presentation forms, U+FB00 (ff) to U+FB06 (st).
PRESENTATION_FORMS = range(0xFB00, 0xFB07)

_CMAP_SECTION = re.compile(rb"begin(bfchar|bfrange)(.*?)end\1", re.DOTALL)
_CMAP_TOKEN = re.compile(rb"<([0-9A-Fa-f\s]*)>|\[|\]")


def _hex(token: bytes) -> bytes:
    return bytes.fromhex(re.sub(rb"\s+", b"", token).decode("ascii"))


def _destinations(cmap: bytes) -> Iterator[tuple[bytes, int]]:
    """Each destination string a `ToUnicode` CMap can produce, as (UTF-16BE, run).

    `run` is how many consecutive values the string stands for: a `bfrange`
    with one destination increments its last code unit across the range
    (ISO 32000-1 §9.10.3), so `<0060> <0063> <FB00>` yields U+FB00 to U+FB03.
    """
    for kind, body in _CMAP_SECTION.findall(cmap):
        # A hex string's digits, or the bracket itself.
        tokens = [
            match.group(0) if match.group(1) is None else match.group(1)
            for match in _CMAP_TOKEN.finditer(body)
        ]
        if kind == b"bfchar":
            for destination in tokens[1::2]:
                yield _hex(destination), 1
            continue
        index = 0
        while index + 2 < len(tokens):
            low, high = (
                int.from_bytes(_hex(tokens[index]), "big"),
                int.from_bytes(_hex(tokens[index + 1]), "big"),
            )
            if tokens[index + 2] == b"[":
                index += 3
                while index < len(tokens) and tokens[index] != b"]":
                    yield _hex(tokens[index]), 1
                    index += 1
                index += 1
            else:
                yield _hex(tokens[index + 2]), max(high - low + 1, 0)
                index += 3


def _forms_in(cmap: bytes) -> set[str]:
    found: set[str] = set()
    for destination, run in _destinations(cmap):
        if len(destination) < 2 or run < 1:
            continue
        head, last = destination[:-2], int.from_bytes(destination[-2:], "big")
        found.update(
            ch
            for ch in head.decode("utf-16-be", errors="ignore")
            if ord(ch) in PRESENTATION_FORMS
        )
        low, high = (
            max(last, PRESENTATION_FORMS.start),
            min(last + run - 1, PRESENTATION_FORMS.stop - 1),
        )
        found.update(chr(code) for code in range(low, high + 1))
    return found


def _fonts(resources: Any, seen: set[int]) -> Iterator[DictionaryObject]:
    """Every font a resource dictionary uses, including inside form XObjects."""
    if isinstance(resources, IndirectObject):
        if resources.idnum in seen:
            return
        seen.add(resources.idnum)
    resources = resources.get_object() if resources is not None else None
    if not isinstance(resources, DictionaryObject):
        return
    fonts = resources.get("/Font")
    for reference in fonts.get_object().values() if fonts is not None else ():
        if isinstance(reference, IndirectObject):
            if reference.idnum in seen:
                continue
            seen.add(reference.idnum)
        font = reference.get_object()
        if isinstance(font, DictionaryObject):
            yield font
    xobjects = resources.get("/XObject")
    for reference in xobjects.get_object().values() if xobjects is not None else ():
        xobject = reference.get_object()
        if isinstance(xobject, DictionaryObject) and xobject.get("/Subtype") == "/Form":
            if isinstance(reference, IndirectObject):
                if reference.idnum in seen:
                    continue
                seen.add(reference.idnum)
            yield from _fonts(xobject.get("/Resources"), seen)


def ligature_forms(pdf: Path) -> list[str]:
    """The ligature presentation forms a text extractor can read out of `pdf`.

    Read from each font's `ToUnicode` map rather than by extracting the text:
    Chromium writes a map entry only for glyphs the document uses, so this
    answers the same question at the cost of one pass over the fonts instead
    of a layout pass over every page.
    """
    found: set[str] = set()
    seen: set[int] = set()
    for page in PdfReader(str(pdf)).pages:
        for font in _fonts(page.get("/Resources"), seen):
            to_unicode = font.get("/ToUnicode")
            if to_unicode is None:
                continue
            if isinstance(to_unicode, IndirectObject):
                if to_unicode.idnum in seen:
                    continue
                seen.add(to_unicode.idnum)
            found |= _forms_in(to_unicode.get_object().get_data())
    return sorted(found)


def ligature_warning(forms: list[str]) -> str:
    """The warning for a text layer that still carries ligatures.

    The example is the first form found, spelled out, so the sentence is true
    of this file rather than of the class of problem.
    """
    example = forms[0]
    return (
        "The PDF's text layer maps glyphs to the ligature characters "
        f"{' '.join(forms)}: searching or extracting the PDF's text can return "
        f'"{example}" where the page shows "{unicodedata.normalize("NFKC", example)}", '
        "and miss the words that contain it. WebShot turns ligatures off before "
        "printing, so these came from text it could not reach (a frame it could "
        "not script, a closed shadow root, a pseudo-element's own override) or "
        "from text that contains these characters itself."
    )
