"""Keep Chromium from rasterizing a gradient's tile across the whole printed page.

Skia's PDF backend writes an opaque CSS gradient as a vector shading, but it
has no vector form for a gradient with a translucent stop, or for a gradient
Blink paints as a repeating tile (a picture shader). It rasterizes either one
instead, at one pixel per point, over the bounds of the current clip, which
for a page is the whole printable area, and it never shares the result
between draws ("Don't bother to de-dup fallback shader", SkPDFShader.cpp).

The translucent case alone costs a few kilobytes a layer: the raster holds one
soft blob and compresses to almost nothing. The repeating case is what makes a
file large. A raster of a repeating tile is a page *full of tiles*, of which
only the box's own rectangle is ever painted. Measured with Playwright 1.64's
Chromium (Skia/PDF m156): Blink takes that path for a background layer above
the bottom one when its single tile does not cover the box's pixel-snapped
painting area, which a box whose width is not a whole number of pixels is
enough for; the bottom layer, and the same box at a whole-pixel width, print
one blob. A page whose buttons carried ten translucent radial-gradient layers
each printed 230 such rasters, 22 MB for five pages; the same page with this
pass is 2.6 MB.

This pass sets `no-repeat` on a gradient layer only where a repeat could not
paint anything a reader sees: the tile is the whole positioning area (`auto`,
`100% 100%`, `cover` or `contain`, positioned by percentages, so it starts at
the area's edge), and every pixel of the painting area outside it lies under
an opaque solid border. What a repeat added there was the opposite edge of the
tile, under the border or in a sliver narrower than a pixel. A layer that is a
real pattern — a tile smaller than its box, or one offset into it — and a box
whose border shows what is under it (dashed, translucent) keep their repeat
and their full-page rasters: changing them would change the page.

Like the ligature pass, the rewrite is inline and `!important`, and a
pseudo-element, which takes no inline style, is reached through a marked
element and a constructed stylesheet adopted into each open shadow root. It
runs after the bundle is built, so no mark reaches it, and before the render
and every print that checks it, so they all print the same page (docs/09
P103).
"""

from __future__ import annotations

import logging

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page as AsyncPage

LOGGER = logging.getLogger("webshot")

#: The rules a marked element's pseudo-elements take their rewritten repeat
#: from. The values are custom properties the script sets on the element,
#: which its pseudo-elements inherit.
UNTILED_PSEUDO_CSS = """
[data-webshot-untiled~='before']::before {
    background-repeat: var(--webshot-before-repeat) !important;
}
[data-webshot-untiled~='after']::after {
    background-repeat: var(--webshot-after-repeat) !important;
}
"""

UNTILE_GRADIENTS_SCRIPT = r"""(css) => {
    const sheet = new CSSStyleSheet();
    sheet.replaceSync(css);
    const roots = [document];
    const collect = root => {
        for (const element of root.querySelectorAll('*')) {
            if (element.shadowRoot) { roots.push(element.shadowRoot); collect(element.shadowRoot); }
        }
    };
    collect(document);
    // A computed multi-layer value, split at its top-level commas.
    const layers = value => {
        const out = [];
        let depth = 0, start = 0;
        for (let i = 0; i < value.length; i++) {
            const c = value[i];
            if (c === '(') depth++;
            else if (c === ')') depth--;
            else if (c === ',' && depth === 0) { out.push(value.slice(start, i).trim()); start = i + 1; }
        }
        out.push(value.slice(start).trim());
        return out;
    };
    const GRADIENT = /^(-webkit-)?(repeating-)?(linear|radial|conic)-gradient\(/;
    const WHOLE_AREA = new Set(['auto', 'auto auto', '100% 100%', 'cover', 'contain']);
    const TILED = new Set(['repeat', 'repeat-x', 'repeat-y']);
    const fromEdge = value => /^-?[\d.]+%$/.test(value);
    // A border hides what is painted under it only when it is drawn solid
    // and opaque; a side with no border hides nothing and needs to.
    const bordersHide = style => style.borderImageSource === 'none' &&
        ['Top', 'Right', 'Bottom', 'Left'].every(side => {
            const kind = style[`border${side}Style`];
            if (kind === 'none' || kind === 'hidden' || parseFloat(style[`border${side}Width`]) === 0) return true;
            if (kind !== 'solid') return false;
            const color = /^rgba?\(([^)]*)\)$/.exec(style[`border${side}Color`]);
            if (!color) return false;
            const parts = color[1].split(/[\s,\/]+/).filter(Boolean);
            return parts.length === 3 || parseFloat(parts[3]) === 1;
        });
    // Whether the painting area is the positioning area, give or take what
    // an opaque border covers.
    const paintsOnlyTheTile = (style, origin, clip) => {
        if (clip === 'text') return false;
        if (origin === 'border-box') return true;
        if (origin === 'padding-box') return clip !== 'border-box' || bordersHide(style);
        return clip === 'content-box';
    };
    // The box's background-repeat with each safely untiled layer rewritten,
    // or null when no layer qualifies.
    const untiled = style => {
        const images = layers(style.backgroundImage);
        if (!images.some(image => GRADIENT.test(image))) return null;
        const at = (value, i) => { const list = layers(value); return list[i % list.length]; };
        let changed = false;
        const next = images.map((image, i) => {
            const repeat = at(style.backgroundRepeat, i);
            if (!GRADIENT.test(image) || !repeat.split(' ').some(token => TILED.has(token))) return repeat;
            if (!WHOLE_AREA.has(at(style.backgroundSize, i))) return repeat;
            if (!fromEdge(at(style.backgroundPositionX, i)) || !fromEdge(at(style.backgroundPositionY, i))) return repeat;
            if (at(style.backgroundAttachment, i) !== 'scroll') return repeat;
            if (!paintsOnlyTheTile(style, at(style.backgroundOrigin, i), at(style.backgroundClip, i))) return repeat;
            changed = true;
            return 'no-repeat';
        });
        return changed ? next.join(', ') : null;
    };
    // The root element's background, or the body's when the root has none,
    // paints the whole canvas while its tile is sized by the root's box
    // (CSS Backgrounds 3, 2.11.2), so its repeat is what fills the canvas
    // below a short page's content. That box is not one whose painting area
    // is its positioning area, and is left as it is. Its pseudo-elements are
    // ordinary boxes.
    const html = document.documentElement;
    const rootStyle = html ? getComputedStyle(html) : null;
    const canvas = new Set([html]);
    if (rootStyle && rootStyle.backgroundImage === 'none' &&
        /^(transparent|rgba\(0, 0, 0, 0\))$/.test(rootStyle.backgroundColor)) {
        canvas.add(document.body);
    }
    let count = 0;
    for (const root of roots) {
        let pseudos = false;
        for (const element of root.querySelectorAll('*')) {
            const own = canvas.has(element) ? null : untiled(getComputedStyle(element));
            if (own) { element.style.setProperty('background-repeat', own, 'important'); count++; }
            const marks = [];
            for (const pseudo of ['before', 'after']) {
                const style = getComputedStyle(element, `::${pseudo}`);
                if (style.content === 'none' || style.content === 'normal') continue;
                const value = untiled(style);
                if (!value) continue;
                element.style.setProperty(`--webshot-${pseudo}-repeat`, value);
                marks.push(pseudo);
                count++;
            }
            if (marks.length) { element.setAttribute('data-webshot-untiled', marks.join(' ')); pseudos = true; }
        }
        if (pseudos) root.adoptedStyleSheets = [...root.adoptedStyleSheets, sheet];
    }
    return count;
}"""


async def untile_gradients(page: AsyncPage) -> int:
    """Untile, in every frame, each gradient layer a repeat adds nothing to.

    Returns how many boxes (elements and pseudo-elements) were rewritten. A
    frame that cannot be scripted is skipped: what it costs is file size, and
    the frame prints as it would have.
    """
    count = 0
    for frame in page.frames:
        try:
            count += await frame.evaluate(UNTILE_GRADIENTS_SCRIPT, UNTILED_PSEUDO_CSS)
        except PlaywrightError as exc:
            LOGGER.debug("Could not untile gradients in frame %s: %s", frame.url, exc)
    return count
