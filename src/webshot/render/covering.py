"""Find page text that a fixed or sticky box prints over, and say so.

A box fixed to the window stays put on screen while the page scrolls under it,
so whatever it covers can be scrolled into view. A PDF cannot scroll, and
Chromium prints a fixed box on every page, at the same place on each, over
whatever text the page has there. A sticky box prints once, where the window
left it: one stuck to the window's bottom edge was drawn about a window's
height down the page, over the text there, with a gap where it belongs. Either
way the PDF shows less of the page than the page does, and before this check
nothing said so (docs/09 P79-4, P83).

This module measures it *after* the render, as `clipping.py` does, and warns
once per box. The measurement moves and hides nothing. In clean mode the
pipeline may then hide a fixed box the measurement found over the text and
print the page again (`hide_covering_boxes`, docs/09 P123-6); every other box
is left to the reader, who can pass `--exclude`.

The measurement is made on a second print, not on the screen, because the two
lay the page out differently: pagination pushes every line that would cross a
page break onto the next page, so where a fixed box falls on page five is not
where it falls on the screen at five window heights. For that print, each box
paints in a colour of its own, its text, backgrounds and borders included, at
the alpha it had, so the PDF itself says which objects are the box's. Read back
in paint order, a character of the page's text is covered when one of the box's
objects is drawn *later* and over the character's centre, at least half
opaque. The order is what makes z-order count: text a page raises above the box
is drawn after it. The alpha is what makes `opacity` count: Chromium draws a
group with `opacity` as one, at that alpha. And `pointer-events`, which decides
what a click reaches, decides nothing that prints.

The colours are set by a stylesheet and attributes, never in a `style`
attribute, which a page's own rules can read (P83-4). Between marking and the
print, a check that no element's layout moved stands guard, and the page gets
its own styles back before anything else reads it.

The boxes are found and marked at the width the page prints at. Chromium
lays the print out at the width of the paper, where a page's media queries
can give it another layout: a news page whose header bar is fixed only in its
narrow layout printed that bar over the first lines of every page, while at
the capture's 1440 px window the fixed box was a different element, and the
check measured the wrong one (docs/09 P123-1). Its colours are another
layout's too: a bar given its background only in the narrow layout would be
marked with none. So the window is narrowed to the printed width, the boxes
are found and their colours read and set there, and the window is put back
before the measuring print. The layout check that guards the marking compares
the page with how it was before the window was narrowed, so it covers that
round trip too. Where the width cannot be read, the boxes are those of the
window's width, as they were before.

A box's paint is told apart by its colour, and by its place in the paint
order. A fixed or sticky box is a stacking context, so Chromium paints all of
it at once: everything drawn between the box's first and last object is the
box's, whether or not its colour could be set. So paint no stylesheet reaches
is still the box's, such as a closed shadow root's, whose contents keep their
own colours (P123-2). Two marks bracket each box for that, a pseudo-element at
the bottom of its stacking order and one at the top, a pixel each, drawn too
faintly to cover anything.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page as AsyncPage

from ..config import CaptureOptions
from .pdf import print_geometry
from .pdfium_bridge import (
    Box,
    Glyph,
    Ink,
    PageInk,
    PdfInkError,
    page_characters,
    page_count,
    read_ink,
)

LOGGER = logging.getLogger("webshot")

#: How opaque a box's paint must be to count as covering what is under it. A
#: tint a reader sees through hides nothing; text read through a half-opaque
#: panel is already hard to read.
COVER_ALPHA = 0.5

#: The attribute each painted element of a box carries while it is measured.
INK_ATTRIBUTE = "data-webshot-ink"

#: The attribute the outermost element of each box carries while it is
#: measured, for the two marks that bracket its paint.
BOX_ATTRIBUTE = "data-webshot-ink-box"

#: How opaque the marks that bracket a box's paint are drawn: enough for
#: Chromium to print them, far too faint to count as covering (`COVER_ALPHA`).
BRACKET_ALPHA = 0.02

#: The colours boxes are painted in count up from this one, skipping any the
#: page already uses, so no object of the page's own reads as a box's.
INK_BASE = (0x05, 0xF3, 0x2B)

#: An id no page gives an SVG filter: `filter: url(#...)` naming it draws the
#: element unfiltered, and still makes it a containing block, as its own
#: filter did.
NO_FILTER_ID = "webshot-ink-no-filter"

#: How much of the covered text a warning quotes.
SAMPLE_CHARS = 60

#: Find each fixed or sticky box that prints, and paint it in a colour of its
#: own for the measuring print. Returns one record per box, in document order.
#:
#: A box is the outermost element whose computed `position` is `fixed` or
#: `sticky` and which has a box at all, in the document or an open shadow root.
#: Whatever the capture hid (`data-webshot-hidden`: the consent managers P79
#: names, the noise clean mode removes, `--exclude`) is `display: none` and has
#: no box, so it is never a candidate.
#:
#: Every element in a box, through its open shadow roots, is given an
#: attribute, and an adopted sheet gives each one `!important` declarations at
#: a specificity no page rule reaches: each colour it and its `::before`,
#: `::after` and `::marker` paint with becomes the box's colour at that
#: colour's own alpha. Shadows are switched off, because a shadow is drawn in
#: its own colour before the box. A filter is swapped for a reference to no
#: filter, because any filter function makes Chromium print the box as a
#: picture, with no colour left to read, and `none` would move the box's fixed
#: descendants. A replaced element (a picture, a video, a canvas, a frame) and
#: an element with a background image paint their whole box in the colour,
#: because their pixels carry no colour of the box's. Transitions are off so
#: the new colours hold at once.
#:
#: Nothing is written to a `style` attribute. WordPress's block styles give an
#: element whose inline style names a border colour a solid border
#: (`html :where([style*="; border-top-color"]) { border-top-style: solid; }`,
#: and the same for each side), so an inline colour grew an empty sticky box
#: on a WordPress blog by 6px and drew it over a headline (docs/09 P83-4).
#: Elements sharing the same declarations share one rule, so a box of
#: thousands of elements adds a handful of rules.
#:
#: Every computed style is read before anything is written, then each painted
#: colour is read back. A box whose colours did not take is reported, never
#: measured as covering nothing. The layout of every element is recorded too,
#: for `LAYOUT_MOVED_SCRIPT` to compare once the page has had its turn: the
#: layout `RECORD_LAYOUT_SCRIPT` took before the printed-width round trip
#: when there was one, so that round trip is held to the same test.
#:
#: Run with the window at the width the page prints at, so the boxes and their
#: colours are those of the printed layout (P123-1). The outermost element of
#: each box gets `boxAttribute` and two marks, a `::before` at the bottom of
#: its stacking order and an `::after` at the top, each a pixel in the box's
#: colour at `BRACKET_ALPHA`, so the PDF says where the box's paint starts and
#: ends (P123-2). A pseudo-element the box already draws keeps its own
#: declarations, and a replaced element has none: those boxes are bracketed by
#: their coloured paint alone.
#:
#: Each record also says how much text the box holds and whether it holds the
#: content clean mode isolated, for the decision of whether clean mode may
#: hide it (P123-6).
MARK_BOXES_SCRIPT = r"""({attribute, base, noFilter,
                         boxAttribute = 'data-webshot-ink-box', bracketAlpha = 0.02}) => {
    const up = node => node.assignedSlot || node.parentElement
        || (node.parentNode instanceof ShadowRoot ? node.parentNode.host : null);
    const top = node => node === document.body || node === document.documentElement;
    const all = [];
    const roots = new Map();
    const collect = root => {
        for (const element of root.querySelectorAll('*')) {
            all.push(element);
            roots.set(element, root);
            if (element.shadowRoot) collect(element.shadowRoot);
        }
    };
    collect(document);
    // Colours as the PDF carries them, in sRGB bytes, whatever syntax the
    // page wrote them in.
    const context = document.createElement('canvas').getContext('2d');
    const bytes = new Map();
    const rgbOf = value => {
        if (!value || value === 'none' || value.startsWith('url(')) return null;
        if (bytes.has(value)) return bytes.get(value);
        context.fillStyle = '#000000';
        context.fillStyle = value;
        const normal = context.fillStyle;
        let found = null;
        let match = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(normal);
        if (match) found = match.slice(1, 4).map(part => parseInt(part, 16));
        else if ((match = /^rgba?\(\s*([\d.]+),\s*([\d.]+),\s*([\d.]+)/.exec(normal))) {
            found = match.slice(1, 4).map(Number);
        }
        bytes.set(value, found);
        return found;
    };
    const alphaOf = value => {
        if (!value || value === 'none' || value === 'transparent') return 0;
        let match = /\/\s*([\d.]+)(%?)\s*\)\s*$/.exec(value);
        if (match) return match[2] ? Number(match[1]) / 100 : Number(match[1]);
        match = /^rgba\(([^)]*)\)$/.exec(value);
        if (match) {
            const parts = match[1].split(',');
            if (parts.length === 4) return Number(parts[3]);
        }
        return 1;
    };
    const COLORS = ['color', '-webkit-text-fill-color', '-webkit-text-stroke-color',
        'text-decoration-color', 'text-emphasis-color', 'outline-color', 'column-rule-color',
        'border-top-color', 'border-right-color', 'border-bottom-color', 'border-left-color'];
    const PLAIN = ['transition: none', 'text-shadow: none', 'box-shadow: none'];
    const REPLACED = new Set(['img', 'video', 'canvas', 'iframe', 'embed', 'object']);
    // One pass: every colour the page paints with, the layout of every
    // element, and the boxes, outermost first. `all` is in document order
    // with each shadow root's contents right after its host, so an outer box
    // is always chosen before what it holds.
    const used = new Set();
    const candidates = [];
    const chosen = new Set();
    for (const element of all) {
        const style = getComputedStyle(element);
        for (const name of [...COLORS, 'background-color', 'fill', 'stroke']) {
            const rgb = rgbOf(style.getPropertyValue(name));
            if (rgb) used.add(rgb.join(','));
        }
        const position = style.position;
        if (position !== 'fixed' && position !== 'sticky') continue;
        if (!element.getClientRects().length) continue;
        let inside = false;
        for (let node = up(element); node && !inside; node = up(node)) inside = chosen.has(node);
        if (inside) continue;
        chosen.add(element);
        candidates.push({element, position});
    }
    const layout = element => element instanceof HTMLElement
        ? [element.offsetLeft, element.offsetTop, element.offsetWidth, element.offsetHeight].join(',')
        : null;
    // The layout from before the printed-width round trip, when there was
    // one. An element that was not there then is a change in itself.
    const before = window.__webshotLayoutBefore || null;
    delete window.__webshotLayoutBefore;
    const recorded = element => before ? (before.has(element) ? before.get(element) : 'new') : layout(element);
    let next = 0;
    const nextCode = () => {
        for (;;) {
            const n = next++;
            const rgb = [((n >> 16) & 255) ^ base[0], ((n >> 8) & 255) ^ base[1], (n & 255) ^ base[2]];
            if (!used.has(rgb.join(','))) return rgb;
        }
    };
    // Classes even beside an id: they are what an `--exclude` is most often
    // written against. An id the page makes up on each load is left out, since
    // a selector naming it matches nothing the next time: a slide-out's id was
    // 32 hex digits, different in each run, and two boxes' ids were load
    // timestamps (docs/09 P101-2). Generated: a leading digit, 16 or more hex
    // digits, or a UUID.
    const generated = id => /^\d/.test(id) || /^[0-9a-f]{16,}$/i.test(id)
        || /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id);
    const stableId = element => element.id && !generated(element.id) ? element.id : '';
    const step = element => {
        const id = stableId(element) ? `#${CSS.escape(element.id)}` : '';
        const classes = [...element.classList].slice(0, 3).map(name => `.${CSS.escape(name)}`);
        return element.localName + id + classes.join('');
    };
    const describe = element => {
        const parent = up(element);
        const selector = stableId(element) || !parent || top(parent) ? step(element) : `${step(parent)} > ${step(element)}`;
        return selector.length > 160 ? `${selector.slice(0, 159)}…` : selector;
    };
    const subtree = element => {
        const found = [element];
        const walk = root => {
            for (const node of root.querySelectorAll('*')) {
                found.push(node);
                if (node.shadowRoot) walk(node.shadowRoot);
            }
        };
        if (element.shadowRoot) walk(element.shadowRoot);
        walk(element);
        return found;
    };
    const high = `:is(${'#webshot-ink'.repeat(8)}, *)`;
    const important = parts => parts.map(part => `${part} !important`).join('; ');
    const rules = [];
    const plans = [];
    candidates.forEach((candidate, number) => {
        const code = nextCode();
        const coded = alpha => `rgba(${code[0]}, ${code[1]}, ${code[2]}, ${alpha})`;
        const keys = new Map();
        const elements = [];
        for (const element of subtree(candidate.element)) {
            const style = getComputedStyle(element);
            const own = [...PLAIN];
            // Any filter function, even `opacity(1)`, makes Chromium print the
            // element as a picture. A reference to no filter at all prints it
            // as it is, and like any filter keeps the element the containing
            // block of its fixed descendants: `none` would move them.
            if (style.filter !== 'none') own.push(`filter: url("#${noFilter}")`);
            const expect = ['color'];
            for (const name of COLORS) own.push(`${name}: ${coded(alphaOf(style.getPropertyValue(name)))}`);
            if (REPLACED.has(element.localName) || style.backgroundImage !== 'none') {
                own.push('background-image: none', `background-color: ${coded(1)}`);
                expect.push('background-color');
            } else {
                const alpha = alphaOf(style.backgroundColor);
                own.push(`background-color: ${coded(alpha)}`);
                if (alpha > 0) expect.push('background-color');
            }
            if (element instanceof SVGElement) {
                for (const name of ['fill', 'stroke']) {
                    const value = style.getPropertyValue(name);
                    if (!value || value === 'none') continue;
                    own.push(`${name}: ${value.startsWith('url(') ? coded(1) : coded(alphaOf(value))}`);
                }
            }
            const pseudos = [];
            for (const pseudo of ['::before', '::after', '::marker']) {
                const ps = getComputedStyle(element, pseudo);
                if (pseudo === '::marker' ? style.display !== 'list-item'
                    : ps.content === 'none' || ps.content === 'normal') continue;
                const parts = [`color: ${coded(alphaOf(ps.color))}`,
                    `-webkit-text-fill-color: ${coded(alphaOf(ps.getPropertyValue('-webkit-text-fill-color')))}`,
                    'text-shadow: none'];
                if (pseudo !== '::marker') {
                    const image = ps.backgroundImage !== 'none';
                    parts.push(`background-color: ${coded(image ? 1 : alphaOf(ps.backgroundColor))}`,
                        'background-image: none', 'box-shadow: none');
                    if (ps.filter !== 'none') parts.push('filter: none');
                    for (const side of ['top', 'right', 'bottom', 'left']) {
                        const name = `border-${side}-color`;
                        parts.push(`${name}: ${coded(alphaOf(ps.getPropertyValue(name)))}`);
                    }
                }
                pseudos.push([pseudo, parts]);
            }
            const signature = JSON.stringify([own, pseudos]);
            let key = keys.get(signature);
            if (key === undefined) {
                key = `${number}.${keys.size}`;
                keys.set(signature, key);
                rules.push(`[${attribute}="${key}"]${high} { ${important(own)} }`);
                for (const [pseudo, parts] of pseudos) {
                    rules.push(`[${attribute}="${key}"]${high}${pseudo} { ${important(parts)} }`);
                }
            }
            elements.push({element, key, expect, own});
        }
        // The two marks that bracket the box's paint (P123-2): absolutely
        // placed in its middle, so they move nothing and no rounded corner
        // clips them away.
        const root = candidate.element;
        if (!REPLACED.has(root.localName)) {
            for (const [pseudo, layer] of [['::before', -2147483647], ['::after', 2147483647]]) {
                const content = getComputedStyle(root, pseudo).content;
                if (content !== 'none' && content !== 'normal') continue;
                rules.push(`[${boxAttribute}="${number}"]${high}${pseudo} { ${important([
                    'content: ""', 'display: block', 'position: absolute', 'top: 50%', 'left: 50%',
                    'width: 1px', 'height: 1px', 'margin: 0', 'padding: 0', 'border: 0',
                    'float: none', 'transform: none', 'filter: none', 'opacity: 1',
                    'clip-path: none', 'visibility: visible', 'pointer-events: none',
                    `background: ${coded(bracketAlpha)}`, `z-index: ${layer}`,
                ])} }`);
            }
        }
        plans.push({candidate, code, elements});
    });
    // The page's own pictures cannot cover anything and cannot be covered:
    // only the boxes' paint and the page's text are read. Hidden, they cost
    // the measuring print nothing (docs/09 P83-5). Hiding paints nothing new
    // and moves nothing. Frames stay: their text is the page's.
    rules.push(`:is(img, video, canvas, image):not([${attribute}]) { visibility: hidden !important }`,
        `:not([${attribute}]) { background-image: none !important }`);
    // Kept before the first write, so whatever happens below can be undone.
    const sheet = new CSSStyleSheet();
    const state = {attribute, boxAttribute, sheet, marked: [], boxes: [], roots: [], inlined: [],
                   all, describe, layout: all.map(recorded), measure: layout};
    window.__webshotInk = state;
    const adopted = new Set([document]);
    plans.forEach((plan, number) => {
        for (const {element, key} of plan.elements) {
            element.setAttribute(attribute, key);
            state.marked.push(element);
            adopted.add(roots.get(element) || document);
        }
        plan.candidate.element.setAttribute(boxAttribute, String(number));
        state.boxes.push(plan.candidate.element);
    });
    sheet.replaceSync(rules.join('\n'));
    for (const root of adopted) {
        root.adoptedStyleSheets = [...root.adoptedStyleSheets, sheet];
        state.roots.push(root);
    }
    // A rule the parser refused is dropped without an error, and what it was
    // for would keep its own colours.
    const parsed = sheet.cssRules.length === rules.length ? null
        : `${rules.length - sheet.cssRules.length} of ${rules.length} rules did not parse`;
    const kept = (element, expect, code) => {
        const style = getComputedStyle(element);
        for (const name of expect) {
            const value = style.getPropertyValue(name);
            const rgb = rgbOf(value);
            if (!rgb || rgb.join(',') !== code.join(',')) return `${describe(element)} kept ${name} ${value}`;
        }
        return null;
    };
    // A declaration the page made `!important` in an element's own `style`
    // attribute beats every rule of a sheet, `!important` or not: an ad
    // slot's `background: rgb(250, 250, 250) !important` kept its colour, and
    // the box printed at the foot of every page went unmeasured (docs/09
    // P97-3). For such an element only, each of its declarations that the
    // attribute holds as `!important` is written there in its place, and the
    // attribute's text is put back exactly afterwards. A page rule that reads
    // the attribute (P83-4) is what the layout check below stands guard
    // against, as it does for the sheet.
    const inline = (element, own) => {
        let wrote = false;
        for (const declaration of own) {
            const at = declaration.indexOf(':');
            const name = declaration.slice(0, at).trim();
            if (element.style.getPropertyPriority(name) !== 'important') continue;
            if (!wrote) state.inlined.push([element, element.getAttribute('style')]);
            wrote = true;
            element.style.setProperty(name, declaration.slice(at + 1).trim(), 'important');
        }
        return wrote;
    };
    return plans.map(({candidate, code, elements}) => {
        let unmarked = parsed;
        for (const {element, expect, own} of unmarked ? [] : elements) {
            unmarked = kept(element, expect, code);
            if (unmarked && inline(element, own)) unmarked = kept(element, expect, code);
            if (unmarked) break;
        }
        // What the name matches as an `--exclude`: this box alone, other
        // elements too (a bare `div`, which a made-up id left behind, would
        // hide every `div` on the page), or nothing (a name cut short, or a
        // box inside a shadow root, which `--exclude` does not reach). Review
        // of #312.
        const name = describe(candidate.element);
        let reach = 'unmatched';
        try {
            const matched = [...document.querySelectorAll(name)];
            if (matched.length === 1 && matched[0] === candidate.element) reach = 'alone';
            else if (matched.some(element => element !== candidate.element)) reach = 'shared';
        } catch (error) { reach = 'unmatched'; }
        // The text the box shows, not what it keeps hidden: a header's closed
        // menus are not text a reader of the page would lose with it.
        const characters = (candidate.element.innerText || '').replace(/\s+/g, '').length;
        const holdsContent = candidate.element.matches('[data-webshot-root="true"]')
            || candidate.element.querySelector('[data-webshot-root="true"]') !== null;
        return {name, position: candidate.position, code, elements: elements.length,
                unmarked, reach, characters, holdsContent};
    });
}"""

#: Each element's layout, as `MARK_BOXES_SCRIPT` records it, before the window
#: is narrowed to the printed width for the marking, so the marking's layout
#: check compares the page put back with the page before (P123-1).
RECORD_LAYOUT_SCRIPT = r"""() => {
    const layout = new Map();
    const collect = root => {
        for (const element of root.querySelectorAll('*')) {
            layout.set(element, element instanceof HTMLElement
                ? [element.offsetLeft, element.offsetTop, element.offsetWidth, element.offsetHeight].join(',')
                : null);
            if (element.shadowRoot) collect(element.shadowRoot);
        }
    };
    collect(document);
    window.__webshotLayoutBefore = layout;
    return layout.size;
}"""

#: A frame, or 100 ms where a page that is not being drawn never gives one
#: (docs/09 P37), then whatever that frame queued: long enough for a resize to
#: be laid out and for the handlers the page runs on it at once.
SETTLE_SCRIPT = r"""async () => {
    await Promise.race([new Promise(resolve => requestAnimationFrame(() => resolve())),
                        new Promise(resolve => setTimeout(resolve, 100))]);
    await new Promise(resolve => setTimeout(resolve, 0));
}"""

#: How many fixed or sticky boxes the page has, counted as `MARK_BOXES_SCRIPT`
#: chooses them, and nothing else: run just before the
#: render, so the check after it knows a box was printed even when the box is
#: gone by then.
#:
#: At the window's width, where the marking runs at the printed width (P123-1).
#: Reading it here would mean resizing the window, and printing a probe to
#: learn the width, before the render; a page's resize and print handlers
#: then run before it, and the render would no longer be of the page the
#: bundle was taken from (docs/09 P16-1 keeps every check after the render for
#: that reason, and a page whose banner leaves after its first print is a
#: fixture, P97-4). The count is only ever asked one thing: whether every box
#: has gone since the render. A box fixed at the window's width alone is
#: counted and never marked, which costs a measuring print that finds the
#: page unchanged; a box fixed at the printed width alone that leaves between
#: the render and the check is the one the count cannot see.
COUNT_BOXES_SCRIPT = r"""() => {
    const up = node => node.assignedSlot || node.parentElement
        || (node.parentNode instanceof ShadowRoot ? node.parentNode.host : null);
    const chosen = new Set();
    const collect = root => {
        for (const element of root.querySelectorAll('*')) {
            const position = getComputedStyle(element).position;
            if ((position === 'fixed' || position === 'sticky') && element.getClientRects().length) {
                let inside = false;
                for (let node = up(element); node && !inside; node = up(node)) inside = chosen.has(node);
                if (!inside) chosen.add(element);
            }
            if (element.shadowRoot) collect(element.shadowRoot);
        }
    };
    collect(document);
    return chosen.size;
}"""


async def count_printed_boxes(page: AsyncPage) -> int | None:
    """How many fixed or sticky boxes the page prints, or `None` if unread."""
    try:
        return int(await page.evaluate(COUNT_BOXES_SCRIPT))
    except PlaywrightError as exc:
        LOGGER.debug("Counting the fixed and sticky boxes failed: %s", exc)
        return None


#: How many elements' layout changed since `MARK_BOXES_SCRIPT` recorded it,
#: and the first of them with its two positions. Run in a later turn of the
#: page's event loop than the marking, so a page script that reacts to the
#: new attributes has run by then.
LAYOUT_MOVED_SCRIPT = r"""async () => {
    // A frame, or 100ms where a page that is not being drawn never gives one
    // (docs/09 P37), then whatever that frame queued.
    await Promise.race([new Promise(resolve => requestAnimationFrame(() => resolve())),
                        new Promise(resolve => setTimeout(resolve, 100))]);
    await new Promise(resolve => setTimeout(resolve, 0));
    const state = window.__webshotInk;
    if (!state) return {moved: -1, first: null};
    let moved = 0;
    let first = null;
    state.all.forEach((element, index) => {
        const now = state.measure(element);
        if (now === state.layout[index]) return;
        moved += 1;
        if (first === null) first = `${state.describe(element)} from ${state.layout[index]} to ${now}`;
    });
    return {moved, first};
}"""

#: Undo `MARK_BOXES_SCRIPT` exactly. The sheet is first cut to its transition
#: rule, so the colours return without animating; then each `style` attribute
#: written to gets its own text back, while that rule still holds; and only
#: then are the sheet and the attributes removed: that change moves no colour,
#: so nothing animates then either.
RESTORE_BOXES_SCRIPT = r"""() => {
    // The layout from before the window was narrowed, should the marking not
    // have run to take it.
    delete window.__webshotLayoutBefore;
    const state = window.__webshotInk;
    if (!state) return 0;
    delete window.__webshotInk;
    state.sheet.replaceSync(`[${state.attribute}] { transition: none !important }`);
    for (const element of state.marked) void getComputedStyle(element).color;
    for (const [element, style] of state.inlined) {
        if (style === null) element.removeAttribute('style');
        else element.setAttribute('style', style);
    }
    for (const element of state.marked) element.removeAttribute(state.attribute);
    for (const element of state.boxes || []) element.removeAttribute(state.boxAttribute);
    for (const root of state.roots) {
        root.adoptedStyleSheets = root.adoptedStyleSheets.filter(sheet => sheet !== state.sheet);
    }
    return state.marked.length;
}"""


@dataclass(frozen=True, slots=True)
class MarkedBox:
    """A fixed or sticky box, as it was painted for the measuring print."""

    #: A selector naming it: tag, id and up to three classes.
    name: str
    #: `fixed` or `sticky`.
    position: str
    #: The colour its paint carried.
    code: tuple[int, int, int]
    #: Why its colours could not be set, or `None` when they were.
    unmarked: str | None = None
    #: What `name` matches as an `--exclude`: `alone` (this box and nothing
    #: else), `shared` (other elements too) or `unmatched` (no element).
    reach: str = "unmatched"
    #: Characters of text the box holds, spaces not counted.
    characters: int = 0
    #: Whether the box holds the content clean mode isolated.
    holds_content: bool = False


@dataclass(frozen=True, slots=True)
class Covering:
    """The page text one box prints over."""

    name: str
    position: str
    #: The printed pages it covers text on.
    pages: tuple[int, ...]
    #: The printed pages it paints on at all: every page, for a fixed box
    #: Chromium repeats.
    printed: tuple[int, ...]
    #: Characters of the page's text it covers, spaces not counted.
    characters: int
    #: Distinct lines those characters are on.
    lines: int
    #: The first stretch of covered text, as the PDF's text layer has it.
    sample: str
    #: What `name` matches as an `--exclude`: `alone` (this box and nothing
    #: else), `shared` (other elements too) or `unmatched` (no element).
    reach: str = "unmatched"
    #: Characters of text the box itself holds, spaces not counted.
    box_characters: int = 0
    #: Whether the box holds the content clean mode isolated.
    holds_content: bool = False


@dataclass(frozen=True, slots=True)
class CoveredText:
    """What the check found, or why it could not look (docs/09 P83)."""

    #: The fixed and sticky boxes that print, or `None` when the script that
    #: finds them failed, so nothing counted them.
    boxes: int | None = None
    #: How many pages the measuring print had, or `None` when it was not made.
    pages: int | None = None
    coverings: tuple[Covering, ...] = ()
    #: Boxes whose colours could not be set, so were not measured.
    unmarked: tuple[MarkedBox, ...] = ()
    #: Why nothing could be measured at all.
    unmeasured: str | None = None
    #: The measuring print's pages, when it was made and matched the render:
    #: the same layout, with each box's paint told apart by its colour. The
    #: check for text printed over text reads these when it can (P123-4).
    measured_pages: tuple[PageInk, ...] | None = None
    #: The colours the measured boxes were painted in.
    box_codes: frozenset[tuple[int, int, int]] = frozenset()


async def find_covered_text(
    page: AsyncPage,
    options: CaptureOptions,
    rendered: Path,
    header_footer: Mapping[str, Any] | None = None,
    boxes_at_render: int | None = None,
    width: int | None = None,
) -> CoveredText:
    """Measure the page text each fixed or sticky box prints over.

    Run after the render and before anything else changes the page, so the
    measuring print lays the page out exactly as `rendered` was: the same page
    box, the same running header and footer (`header_footer`, the render's
    own), backgrounds on, and nothing changed but colours. Three checks hold
    that: no element's layout may have moved once the page has reacted to the
    marks, `rendered` must have as many pages as the measuring print, and each
    page must hold the same text in both, because a measurement of a different
    page would certify a different PDF. `boxes_at_render`, the count
    `count_printed_boxes` took before the render, makes the third check run
    when every box has gone by now (docs/09 P97-4).

    `width` is the width the render was laid out at
    (`render/clipping.printed_width`). The window is narrowed to it for the
    marking, so the boxes and their colours are those of the printed layout,
    and put back before the measuring print (P123-1). Without it, or without
    a viewport to resize, the boxes are those of the window's width, as
    before that change.
    """
    started = time.monotonic()
    viewport = page.viewport_size
    narrowed = width is not None and viewport is not None and width != viewport["width"]
    try:
        if narrowed and viewport is not None and width is not None:
            await page.evaluate(RECORD_LAYOUT_SCRIPT)
            await page.set_viewport_size({"width": width, "height": viewport["height"]})
            await page.evaluate(SETTLE_SCRIPT)
        try:
            marked = await page.evaluate(
                MARK_BOXES_SCRIPT,
                {
                    "attribute": INK_ATTRIBUTE,
                    "base": list(INK_BASE),
                    "noFilter": NO_FILTER_ID,
                    "boxAttribute": BOX_ATTRIBUTE,
                    "bracketAlpha": BRACKET_ALPHA,
                },
            )
        finally:
            if narrowed and viewport is not None:
                await page.set_viewport_size(viewport)
    except PlaywrightError as exc:
        await _restore(page)
        return CoveredText(
            boxes=None,
            unmeasured=f"the boxes could not be marked: {_first_line(exc)}",
        )
    boxes = [
        MarkedBox(
            name=str(record["name"]),
            position=str(record["position"]),
            code=(
                int(record["code"][0]),
                int(record["code"][1]),
                int(record["code"][2]),
            ),
            unmarked=record["unmarked"],
            reach=str(record["reach"]),
            characters=int(record["characters"]),
            holds_content=bool(record["holdsContent"]),
        )
        for record in marked
    ]
    unmarked = tuple(box for box in boxes if box.unmarked)
    # With no box now, there is nothing to measure, unless the render printed
    # one that has gone since: then whether the page changed is the question.
    gone = 0 if boxes else boxes_at_render or 0
    if (not boxes and not gone) or (boxes and len(unmarked) == len(boxes)):
        await _restore(page)
        return CoveredText(boxes=len(boxes), unmarked=unmarked)
    try:
        moved = await page.evaluate(LAYOUT_MOVED_SCRIPT)
        if moved["moved"]:
            await _restore(page)
            # Said of both steps when both ran: the layout compared is the
            # one from before the printed-width round trip (P123-1).
            done = (
                "laying the page out at its printed width, marking the boxes there "
                "and putting the window back"
                if narrowed
                else "marking the boxes"
            )
            return CoveredText(
                boxes=len(boxes),
                unmarked=unmarked,
                unmeasured=(
                    "the marks were gone before the measuring print"
                    if moved["moved"] < 0
                    else f"{done} moved {moved['moved']} element(s) of the "
                    f"page, the first {moved['first']}"
                ),
            )
        pdf = await page.pdf(
            **print_geometry(options),
            print_background=True,
            tagged=False,
            outline=False,
            **(header_footer or {}),
        )
    except PlaywrightError as exc:
        return CoveredText(
            boxes=len(boxes),
            unmarked=unmarked,
            unmeasured=f"the measuring print failed: {_first_line(exc)}",
        )
    finally:
        await _restore(page)
    try:
        pages = await asyncio.to_thread(read_ink, pdf)
        printed = await asyncio.to_thread(page_count, rendered)
        changed = (
            _changed_text(
                await asyncio.to_thread(page_characters, rendered), pages, boxes
            )
            if len(pages) == printed
            else None
        )
    except PdfInkError as exc:
        return CoveredText(boxes=len(boxes), unmarked=unmarked, unmeasured=str(exc))
    LOGGER.debug(
        "Measured %d fixed or sticky box(es) over %d printed page(s) in %.2f s.",
        len(boxes) - len(unmarked),
        len(pages),
        time.monotonic() - started,
    )
    if len(pages) != printed:
        return CoveredText(
            boxes=len(boxes),
            unmarked=unmarked,
            unmeasured=(
                f"the measuring print had {len(pages)} page(s) and the PDF has "
                f"{printed}, so it did not measure the layout that was printed"
            ),
        )
    if changed is not None:
        if gone:
            changed = (
                f"{_boxes(gone)} {'was' if gone == 1 else 'were'} on the page when "
                f"it was printed and none when it was measured, and {changed}"
            )
        return CoveredText(boxes=len(boxes), unmarked=unmarked, unmeasured=changed)
    if gone:
        return CoveredText(boxes=0)
    unread = unread_text(pages)
    if unread is not None:
        return CoveredText(boxes=len(boxes), unmarked=unmarked, unmeasured=unread)
    return CoveredText(
        boxes=len(boxes),
        pages=len(pages),
        coverings=measure(pages, boxes),
        unmarked=unmarked,
        measured_pages=pages,
        box_codes=frozenset(box.code for box in boxes if box.unmarked is None),
    )


def _changed_text(
    rendered: Sequence[str], pages: Sequence[PageInk], boxes: Sequence[MarkedBox]
) -> str | None:
    """Where the measuring print's text differs from the render's, or `None`.

    Marking changes colours, so each page holds the same characters in both
    prints unless the page itself changed in between. A consent banner that its
    own script hid five seconds after load was printed in the render, mid-fade,
    and gone from the measuring print a moment later, so the check measured a
    page without it and found nothing to report (docs/09 P97-4).

    One difference is by design: a box with a CSS filter prints as a picture,
    with no text, in the render, and as text in the measuring print, where its
    filter is swapped for none (P83-1). So the pages are compared as counts of
    characters, both ways but not alike: every character the render printed
    must be in the measuring print, and every character the measuring print
    drew outside the marked boxes must be in the render. A box gone since the
    render fails the first; a change to the page's own text fails either.
    """
    codes = {box.code for box in boxes if box.unmarked is None}
    for number, (then, page) in enumerate(zip(rendered, pages, strict=True), start=1):
        drawn = {ink.order: ink.color for ink in page.inks if ink.kind == "text"}
        printed = Counter(c for c in then if not c.isspace())
        every = Counter(g.char for g in page.glyphs if not g.char.isspace())
        own = Counter(
            g.char
            for g in page.glyphs
            if not g.char.isspace() and drawn.get(g.order) not in codes
        )
        lost = sum((printed - every).values())
        if lost:
            return (
                f"the page changed between the render and the check: page {number} "
                f"of the PDF holds {lost:,} characters the measuring print does not"
            )
        added = sum((own - printed).values())
        if added:
            return (
                f"the page changed between the render and the check: page {number} "
                f"of the measuring print holds {added:,} characters of the page's own "
                "text that the PDF's does not"
            )
    return None


def unread_text(pages: Sequence[PageInk]) -> str | None:
    """Why the measuring print's text could not be read, or `None` if it could.

    A character is matched to the object that drew it through
    `FPDFText_GetTextObject`. Were that ever to come back empty, every
    character would belong to no object, none could be covered, and every
    capture would read as one with nothing covered; so text drawn with no
    character tied to it fails the check instead of passing it.
    """
    drawn = sum(1 for page in pages for ink in page.inks if ink.kind == "text")
    tied = sum(1 for page in pages for glyph in page.glyphs if glyph.order >= 0)
    if drawn and not tied:
        return (
            f"PDFium read {drawn} text object(s) in the measuring print and tied "
            "no character to any of them"
        )
    return None


async def _restore(page: AsyncPage) -> None:
    try:
        await page.evaluate(RESTORE_BOXES_SCRIPT)
    except PlaywrightError as exc:
        # A page that cannot run this has closed or crashed, and whatever
        # reads it next fails on that, not on colours left behind.
        LOGGER.debug("Restoring the boxes' colours failed: %s", exc)


def _first_line(exc: Exception) -> str:
    return str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__


def measure(
    pages: Sequence[PageInk], boxes: Sequence[MarkedBox]
) -> tuple[Covering, ...]:
    """The page text each box covers, from the measuring print's objects.

    A character is the page's text unless a fixed box drew it. A fixed box's
    own words, and another fixed box's, are page chrome, which covers chrome
    on screen just as it does in print; a sticky box's words are in the page's
    flow, so a fixed box over a sticky table heading does count. Characters
    drawn at less than `COVER_ALPHA`, such as the invisible text layer WebShot
    adds for recognized text, cannot be covered, because they cannot be read
    in the first place.

    An object drawn between a box's first and last object is the box's too,
    whatever its colour: a box is a stacking context, painted all at once, and
    what lies between is paint the marking could not colour, such as a closed
    shadow root's (P123-2). Such an object covers like any of the box's, a
    picture included, since nothing else of the box draws one.
    """
    by_code = {
        box.code: index for index, box in enumerate(boxes) if box.unmarked is None
    }
    covered: dict[int, list[tuple[int, Glyph, tuple[Glyph, ...]]]] = {}
    printed: dict[int, list[int]] = {}
    for page in pages:
        regions: dict[int, list[Ink]] = {}
        drawn_by: dict[int, tuple[int | None, float]] = {}
        spans = box_spans(page, by_code)
        for ink in page.inks:
            owner = by_code.get(ink.color) if ink.color is not None else None
            between = False
            if owner is None:
                owner = _within(spans, ink.order)
                between = owner is not None
            if ink.kind == "text":
                drawn_by[ink.order] = (owner, ink.alpha)
            if (
                owner is not None
                and (ink.kind in ("text", "path") or (between and ink.kind == "image"))
                and ink.alpha >= COVER_ALPHA
                and _area(ink.box) > 0
            ):
                regions.setdefault(owner, []).append(ink)
        if not regions:
            continue
        for owner in regions:
            printed.setdefault(owner, []).append(page.number)
        reach = {owner: _union(inks) for owner, inks in regions.items()}
        for glyph in page.glyphs:
            if glyph.order < 0 or glyph.char.isspace():
                continue
            owner, alpha = drawn_by.get(glyph.order, (None, 0.0))
            if alpha < COVER_ALPHA:
                continue
            if owner is not None and boxes[owner].position == "fixed":
                continue
            for index, inks in regions.items():
                if index == owner or not _contains(reach[index], glyph):
                    continue
                if any(
                    ink.order > glyph.order and _contains(ink.box, glyph)
                    for ink in inks
                ):
                    covered.setdefault(index, []).append(
                        (page.number, glyph, page.glyphs)
                    )
    found = []
    for index, box in enumerate(boxes):
        hits = covered.get(index)
        if not hits:
            continue
        found.append(
            Covering(
                name=box.name,
                position=box.position,
                pages=tuple(sorted({number for number, _, _ in hits})),
                printed=tuple(printed[index]),
                characters=len(hits),
                lines=len(
                    {(number, round(glyph.baseline)) for number, glyph, _ in hits}
                ),
                sample=_sample(hits),
                reach=box.reach,
                box_characters=box.characters,
                holds_content=box.holds_content,
            )
        )
    return tuple(found)


def box_spans(
    page: PageInk, by_code: Mapping[tuple[int, int, int], int]
) -> dict[int, tuple[int, int]]:
    """Each box's first and last object on the page, by its colour."""
    spans: dict[int, tuple[int, int]] = {}
    for ink in page.inks:
        owner = by_code.get(ink.color) if ink.color is not None else None
        if owner is None:
            continue
        first, last = spans.get(owner, (ink.order, ink.order))
        spans[owner] = (min(first, ink.order), max(last, ink.order))
    return spans


def _within(spans: Mapping[int, tuple[int, int]], order: int) -> int | None:
    """The box whose paint `order` falls strictly inside, or `None`."""
    for owner, (first, last) in spans.items():
        if first < order < last:
            return owner
    return None


def _sample(hits: Sequence[tuple[int, Glyph, tuple[Glyph, ...]]]) -> str:
    """The first stretch of covered text: covered characters and the spaces
    between them, on the first covered character's line."""
    number, first, glyphs = min(hits, key=lambda hit: (hit[0], hit[1].index))
    indices = {glyph.index for page, glyph, _ in hits if page == number}
    text = []
    started = False
    for glyph in glyphs:
        if glyph.index < first.index:
            continue
        started = True
        if glyph.char.isspace():
            text.append(" ")
            continue
        if glyph.index not in indices or abs(glyph.baseline - first.baseline) > 1:
            break
        text.append(glyph.char)
    sample = " ".join("".join(text).split()) if started else ""
    return sample if len(sample) <= SAMPLE_CHARS else sample[: SAMPLE_CHARS - 1] + "…"


def _area(box: Box) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _union(inks: Sequence[Ink]) -> Box:
    return (
        min(ink.box[0] for ink in inks),
        min(ink.box[1] for ink in inks),
        max(ink.box[2] for ink in inks),
        max(ink.box[3] for ink in inks),
    )


def _contains(box: Box, glyph: Glyph) -> bool:
    return box[0] <= glyph.x <= box[2] and box[1] <= glyph.y <= box[3]


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _pages(numbers: Sequence[int], total: int) -> str:
    """`page 3`, `both pages`, `all 5 pages`, or `pages 1-3 and 5`."""
    if len(numbers) == 1:
        return f"page {numbers[0]}"
    if len(numbers) == total:
        return "both pages" if total == 2 else f"all {total} pages"
    runs: list[list[int]] = []
    for number in numbers:
        if runs and number == runs[-1][-1] + 1:
            runs[-1].append(number)
        else:
            runs.append([number])
    spans = [f"{run[0]}-{run[-1]}" if len(run) > 1 else str(run[0]) for run in runs]
    listed = (
        spans[0] if len(spans) == 1 else ", ".join(spans[:-1]) + f" and {spans[-1]}"
    )
    return f"pages {listed}"


def _boxes(count: int) -> str:
    return f"{count} fixed or sticky box{'' if count == 1 else 'es'}"


#: How much of the text the page shows a box may itself show for clean mode to
#: hide it once it has been measured over the page's text, as a share and in
#: characters without spaces. A site's header bar, a cookie notice, an ad
#: anchor or a decorative panel shows a few hundred characters; a box past
#: either bound may be the page's own content, laid over a page behind it, and
#: is only reported. A page whose text is all in one fixed box, a notice
#: centred in the window, is one where hiding the box would leave nothing
#: (docs/09 P123-6).
HIDEABLE_SHARE = 0.25
HIDEABLE_CHARACTERS = 1500

PAGE_CHARACTERS_SCRIPT = r"""() => ((document.body && document.body.innerText) || '').replace(/\s+/g, '').length"""

#: Hide each named box that its name matches alone, as `hide_elements` hides
#: what it matches; answer, for each, whether it was hidden. A box that still
#: lays out once marked was not: a page rule that outranks WebShot's hiding
#: one keeps it printing (docs/09 P128). `unmark_shown` takes such a mark off
#: before the bundle, and this one is set after the first render, where it
#: cannot reach, so the mark comes off here, and the box is reported as
#: faithful mode reports it. A mark it finds already set is left alone: one
#: that survived `unmark_shown` is `--exclude`'s. The mark records no
#: `data-webshot-hidden-by`: one that stays is on a box with no layout, which
#: `unmark_shown` would not name even if it ran again.
HIDE_BOXES_SCRIPT = r"""(names) => names.map(name => {
    let matched;
    try { matched = document.querySelectorAll(name); } catch (error) { return false; }
    if (matched.length !== 1) return false;
    const box = matched[0];
    const marked = box.getAttribute('data-webshot-hidden') === 'true';
    box.setAttribute('data-webshot-hidden', 'true');
    if (box.getClientRects().length === 0) return true;
    if (!marked) box.removeAttribute('data-webshot-hidden');
    return false;
})"""


def hideable(result: CoveredText, shown: int) -> list[Covering]:
    """The boxes over the page's text that clean mode may hide, of a page
    showing `shown` characters (docs/09 P123-6).

    A fixed box only: Chromium repeats one on every page, so whatever it is
    on screen it is chrome on paper, while a sticky box prints once, in the
    page's flow, and is the page (a table's header row). Its name must match
    it alone, so it is the box that is hidden. And it must show no more text
    than `HIDEABLE_SHARE` and `HIDEABLE_CHARACTERS` allow, and hold none of the
    content the capture isolated, so it is not the page's own content.
    """
    bound = min(HIDEABLE_CHARACTERS, HIDEABLE_SHARE * shown)
    chosen: list[Covering] = []
    for covering in result.coverings:
        if (
            covering.position == "fixed"
            and covering.reach == "alone"
            and not covering.holds_content
            and covering.box_characters <= bound
            and covering.name not in {box.name for box in chosen}
        ):
            chosen.append(covering)
    return chosen


async def hide_covering_boxes(
    page: AsyncPage, result: CoveredText, walls: Sequence[str] = ()
) -> list[Covering]:
    """Hide the fixed boxes clean mode may remove once measured over the page's
    text, and return them (docs/09 P123-6).

    Five captures in one week printed a fixed box over their text, a header
    bar, a decorative panel, a cookie bar, and each time the `--exclude` the
    warning offered was the right fix, applied by hand. Clean mode removes page
    chrome it knows by name before the bundle; a fixed box the measurement
    finds over the text is page chrome it did not know, so it is removed too,
    and the caller prints the page again. Only the boxes `hideable` allows,
    and only on a page that showed no access wall (`walls`), since a box over
    the text may be part of the wall, and hiding it cannot bring back what
    the site keeps under it. Any other box is reported, as before.

    The bundle was taken before this, so it keeps the box's text; the warning
    the caller writes says so. A page whose text cannot be read hides nothing,
    and its boxes are reported.
    """
    if walls or not result.coverings:
        return []
    try:
        shown = int(await page.evaluate(PAGE_CHARACTERS_SCRIPT))
    except PlaywrightError as exc:
        LOGGER.debug("Reading the page's text length failed: %s", exc)
        return []
    chosen = hideable(result, shown)
    if not chosen:
        return []
    try:
        hidden = await page.evaluate(
            HIDE_BOXES_SCRIPT, [covering.name for covering in chosen]
        )
    except PlaywrightError as exc:
        LOGGER.debug("Hiding the boxes over the page's text failed: %s", exc)
        return []
    return [covering for covering, done in zip(chosen, hidden, strict=True) if done]


def hidden_boxes_message(boxes: Sequence[Covering]) -> str:
    """The manifest warning for the boxes `hide_covering_boxes` hid."""
    named = "; ".join(
        f"{box.name}, over {_plural(box.characters, 'character')} of the page's "
        f"text on {_plural(len(box.pages), 'page')}"
        for box in boxes
    )
    one = len(boxes) == 1
    subject = "a fixed box" if one else f"{len(boxes)} fixed boxes"
    them = "it" if one else "them"
    whose = "the box's" if one else "the boxes'"
    return (
        f"Clean mode hid {subject} that printed over the page's text, and printed "
        f"the page again without {them}: {named}. Chromium prints a fixed box on "
        "every page, so in a PDF it is page chrome, which clean mode removes. The "
        f"bundle was taken before, so it still holds {whose} text; --mode faithful "
        f"prints the page with {them} and names {them} in a warning instead."
    )


def covered_text_warnings(result: CoveredText, walls: Sequence[str] = ()) -> list[str]:
    """The manifest warnings for a measurement: one per box that covers text.

    `walls` names the access walls the page showed (`capture.landing`). On
    such a page a box over the text may be the wall, which the site keeps the
    article under, so the warning does not offer --exclude as the remedy.
    """
    warnings = []
    if result.unmeasured is not None:
        # The count is said only where something counted the boxes: when the
        # script that finds them failed, there is no number to state.
        counted = f" The page has {_boxes(result.boxes)}, so" if result.boxes else ""
        warnings.append(
            "WebShot could not check whether a fixed or sticky box prints over the "
            f"page's text ({result.unmeasured}).{counted or ' So'} the PDF may show "
            "one over its text without a warning."
        )
    # Boxes that would read alike are said once, with their number. A page
    # that ran its welcome ad twice had two fixed boxes of one name over the
    # same text, and the manifest said the same sentence twice, which read as
    # a duplicated warning rather than two boxes (docs/09 P113-4).
    alike: dict[tuple[object, ...], list[Covering]] = {}
    for covering in result.coverings:
        key = (
            covering.position,
            covering.name,
            covering.reach,
            covering.characters,
            covering.lines,
            tuple(covering.pages),
            tuple(covering.printed),
            covering.sample,
        )
        alike.setdefault(key, []).append(covering)
    for group in alike.values():
        covering = group[0]
        total = result.pages or len(covering.printed)
        # A box Chromium repeats on every page is named once, with the pages
        # it covers text on, which are what a reader loses; the pages it
        # prints on are said too where they are more (docs/09 P83-3).
        printed = (
            ""
            if covering.printed == covering.pages
            else f"on {_pages(covering.printed, total)}, and "
        )
        remedy = (
            f"The page showed {' and '.join(walls)}'s access wall, and this box "
            "may be part of it: --exclude would not bring back text the site "
            "keeps under the wall."
            if walls
            else f'Pass --exclude "{covering.name}" to capture the page without '
            "the box."
            if covering.reach == "alone"
            else f"{covering.name} names other elements of the page too, so "
            "--exclude with it would hide them as well; a --css rule written for "
            "this box removes it alone."
            if covering.reach == "shared"
            else "Its name matches no element of the page's document as a "
            "selector (it was cut short, or the box is inside a shadow root), so "
            "--exclude is not offered."
        )
        subject = (
            f"A {covering.position} box, {covering.name}, prints {printed}over "
            if len(group) == 1
            else f"{len(group)} {covering.position} boxes, each named "
            f"{covering.name}, print {printed}each over "
        )
        drawn = (
            "the box is drawn over it"
            if len(group) == 1
            else "the boxes are drawn over it"
        )
        warnings.append(
            f"{subject}"
            f"{_plural(covering.characters, 'character')} of the page's text in "
            f"{_plural(covering.lines, 'line')} on {_pages(covering.pages, total)}, "
            f'starting "{covering.sample}". The text stays in the PDF\'s text layer, '
            f"but on the page {drawn}. {remedy}"
        )
    if result.unmarked:
        named = "; ".join(f"{box.name} ({box.unmarked})" for box in result.unmarked)
        # A fixed box is printed on every page whatever it covers, so that much
        # is known without the measurement (docs/09 P97-3).
        fixed = sum(1 for box in result.unmarked if box.position == "fixed")
        every = (
            ""
            if not fixed
            else " Chromium prints a fixed box on every page, so "
            + ("it is" if len(result.unmarked) == 1 else f"{fixed} of them are")
            + " on every page of the PDF."
        )
        warnings.append(
            f"WebShot could not mark {_boxes(len(result.unmarked))} to check what "
            "page text they print over, so the PDF may show them over its text "
            f"without a warning: {named}.{every}"
        )
    return warnings
