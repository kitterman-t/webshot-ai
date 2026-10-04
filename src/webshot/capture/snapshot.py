"""The page's own account of itself: semantic records, links, metadata, ARIA.

Everything here runs against the *prepared* DOM, so what it returns is what a
reader would have seen.  Two rules are load-bearing:

- **Visibility is decided here, in CAPTURE** (docs/02 §Data contracts): the
  standalone snapshot keeps only elements a reader could see, because it is the
  input to the extraction stage and the parity harness measures both formats on
  the visible-content basis this snapshot defines.  Media elements are kept
  regardless — a `<video>` with no box is still part of the page's record.
- **Sanitization is the Python side's job** (spec §6.5): the browser side
  gathers and filters, but what makes `content.html` script-free,
  handler-free, and scheme-safe is the allowlist below, applied through
  `webshot/sanitize.py` — the one module that imports nh3.  Password values are redacted
  before they can reach any artifact (spec §6.2) — in the records, in the
  snapshot clone, and in the aria snapshot, which reads the live DOM and needs
  its own scrub.

The in-browser block extraction also still runs: its form-field,
embedded-media, and definition records are fidelity docling's HTML backend does
not model (docs/02 fidelity mapping), and `--legacy-bundle` replays it as v2
output until v3.1 removes both.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Collection, Iterator, Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

from ..sanitize import Policy, sanitize, widen_default_tags, with_nh3_defaults
from .session import devtools

if TYPE_CHECKING:
    from .visuals import VisualAsset

#: Tags added to nh3's conservative default set: document landmarks, forms
#: (values are records — spec §6.2 redacts passwords before they get here),
#: and media with their caption tracks.  `script`/`style` stay in nh3's
#: default clean-content set, so they vanish *with* their contents; `iframe`
#: is deliberately absent — its record lives in assets.json, not as an
#: embeddable element in the snapshot. What a frame showed arrives another way:
#: its document, where the page could read it, or else its screenshot as an
#: `<img>`.
SNAPSHOT_EXTRA_TAGS = {
    "main",
    "section",
    "address",
    "tfoot",
    "form",
    "fieldset",
    "legend",
    "label",
    "output",
    "input",
    "textarea",
    "select",
    "option",
    "optgroup",
    "button",
    "video",
    "audio",
    "source",
    "track",
}

#: Attributes allowed on every tag.
SNAPSHOT_GENERIC_ATTRIBUTES = {
    "id",
    "lang",
    "dir",
    "title",
    "role",
    "aria-label",
    "aria-description",
    "aria-describedby",
    "aria-hidden",
}

#: Per-tag attributes the bundle contract depends on. Some entries overlap
#: nh3's defaults on purpose: this dict is the allowlist of record for asset
#: refs, form state, and media tracks, so an upstream default shrinking must
#: not silently shrink the snapshot.
SNAPSHOT_EXTRA_ATTRIBUTES = {
    "img": {"src", "alt", "width", "height"},
    "a": {"href", "hreflang", "name"},
    "time": {"datetime"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan", "scope"},
    "input": {
        "type",
        "name",
        "value",
        "checked",
        "placeholder",
        "readonly",
        "disabled",
    },
    "textarea": {"name", "rows", "cols", "placeholder", "readonly", "disabled"},
    "select": {"name", "multiple", "disabled"},
    "option": {"value", "selected"},
    "video": {"src", "width", "height", "poster", "preload", "controls"},
    "audio": {"src", "preload", "controls"},
    "source": {"src", "type"},
    "track": {"kind", "src", "srclang", "label", "default"},
}

PASSWORD_REDACTION = "[REDACTED PASSWORD]"

#: The snapshot's allowlist. `data-webshot-` carries the asset ids the bridge
#: uses to attach OCR annotations; the browser side strips any page-authored
#: `data-webshot-*` before ours are assigned, so the prefix cannot be spoofed.
SNAPSHOT_POLICY = Policy(
    tags=widen_default_tags(SNAPSHOT_EXTRA_TAGS),
    attributes=with_nh3_defaults(
        {"*": set(SNAPSHOT_GENERIC_ATTRIBUTES), **SNAPSHOT_EXTRA_ATTRIBUTES}
    ),
    generic_prefixes=frozenset({"data-webshot-"}),
)


def sanitize_body_html(body_html: str) -> str:
    """The allowlist pass that makes the snapshot publishable (spec §6.5)."""
    return sanitize(body_html, SNAPSHOT_POLICY)


#: The one <title> element in a standalone document — the head below is the
#: only producer (the sanitizer's allowlist keeps `title` out of body markup),
#: and the extraction bridge strips it from its own input copy with this same
#: regex, so wrap and un-wrap cannot drift apart.
TITLE_ELEMENT_RE = re.compile(r"<title>.*?</title>", re.IGNORECASE | re.DOTALL)


def standalone_document(title: str, language: str, sanitized_body: str) -> str:
    """Wrap the sanitized body as the standalone `content.html` document."""
    lang_attribute = f' lang="{html.escape(language, quote=True)}"' if language else ""
    return (
        f'<!doctype html>\n<html{lang_attribute}>\n<head>\n<meta charset="utf-8">\n'
        f"<title>{html.escape(title)}</title>\n</head>\n<body>\n"
        f"{sanitized_body}\n</body>\n</html>\n"
    )


#: How every stage names the element a record came from: a CSS path from
#: `body`, shortened at the first ancestor with an id.  Defined once and
#: injected into each script that needs it, because three stages now report
#: locators — the snapshot's own records, embedded-video detection, and plain
#: media detection — and three copies of a DOM walk is three chances to
#: disagree about what a locator means.  Null-safe on `parentElement`: a scan
#: that reaches outside `<body>` (an `og:video` `<meta>`, say) must return a
#: locator rather than throw.
#: Whether an element is part of what the capture shows.  Defined here and
#: injected into every script that needs it, for the reason `LOCATOR_JS` is:
#: three stages now ask this question — semantic extraction, the clone filter,
#: and the embedded-walkthrough scan — and the third one asked a *different*
#: question (only `data-webshot-hidden`), so a duplicate player hidden with
#: `display:none` counted as captured content (docs/09 P8-25).
VISIBLE_JS = """element => {
    if (element.closest('[data-webshot-hidden="true"]')) return false;
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden'
        && rect.width > 0 && rect.height > 0;
}"""


#: The flat tree, as a reader sees the page: a shadow host's children are its
#: shadow root's, a `<slot>` holds what is assigned to it or else its own
#: fallback, and a frame the page can script holds its document's body.
#: `children(node, body)` takes the body `frameBody` found for a frame, or
#: null. `trees(root)` is every tree reachable from `root` whether shown or
#: not: it, each open shadow root, and each frame document the page can script,
#: recursively. Shared by the snapshot, the visuals harvest and `HEADING_JS`,
#: so what is photographed, what is read, and the heading it sits under come
#: from the same trees (docs/09 P16-6, P16-9, P16-12).
FLAT_TREE_JS = """(() => {
    const isFrame = node => (node.localName === 'iframe' || node.localName === 'frame')
        && node.namespaceURI === 'http://www.w3.org/1999/xhtml';
    const frameBody = element => {
        try { return element.contentDocument?.body ?? null; } catch { return null; }
    };
    const children = (node, body) => {
        if (node.shadowRoot) return node.shadowRoot.childNodes;
        if (body) return body.childNodes;
        if (node.localName === 'slot' && node.getRootNode().host) {
            const assigned = node.assignedNodes();
            if (assigned.length) return assigned;
        }
        return node.childNodes;
    };
    const trees = root => {
        const found = [root];
        for (let index = 0; index < found.length; index += 1) {
            for (const element of found[index].querySelectorAll('*')) {
                if (element.shadowRoot) found.push(element.shadowRoot);
                if (!isFrame(element)) continue;
                let inner = null;
                try { inner = element.contentDocument; } catch {}
                if (inner) found.push(inner);
            }
        }
        return found;
    };
    return {isFrame, frameBody, children, trees};
})()"""

#: The markers the visuals harvest assigns, and removes wherever a page wrote
#: its own: the top document, every open shadow root, and every document of a
#: frame the page can script, recursively. Those are all the trees the
#: snapshot walks, so in any of them a marker from this list is ours, and the
#: asset mapping the bridge trusts cannot be spoofed from the page (P16-9).
#: `data-webshot-ocr-text` names elements rather than marking them: the
#: harvest removes the page's and inserts its own. `data-webshot-hidden` is
#: not here: it is the prepare stage's, and stays.
HARVEST_MARKERS = (
    "data-webshot-asset-id",
    "data-webshot-visual-index",
    "data-webshot-background-asset",
    "data-webshot-rendered-canvas",
    "data-webshot-frame-index",
    "data-webshot-ocr-text",
)


LOCATOR_JS = """function locate(element) {
    // Two ways this used to emit a selector that matches nothing, both from
    // the same unconditional `body > ` prefix (docs/09 P8-39):
    //
    //   * `body > html > head > meta` for an `og:video` embed. `html` and
    //     `head` are not descendants of `body`, so the manifest's new video
    //     locator did not resolve to the element it named — which is the
    //     finding. A head element is anchored from `head` instead.
    //   * `body > p#para` for a `<p id="para">` nested inside a `<div>`. The
    //     walk stops at the first id and drops the ancestors above it, which
    //     is the intended shortening — but `>` then asserts a parent-child
    //     relationship those dropped ancestors deny. An id is unique, so the
    //     shortened path needs no root at all.
    //
    // Found while fixing the first; both are the same claim ("this locator
    // identifies this element") failing the same way, and fixing one would
    // have left the contract false for the commoner case.
    //
    // An element inside an open shadow root or a same-origin frame is named
    // by the element hosting its tree, then ` >> `, then its path inside that
    // tree: from the shadow root's top, or from the frame document's `body`.
    // `querySelector` crosses neither boundary, so no one CSS selector names
    // such an element. Without this the walk ran off the top of a shadow root
    // and answered `head > …`, and in a frame climbed toward an outer `body`
    // it never reaches (docs/09 P16-6).
    const tree = element.getRootNode();
    const host = tree.host
        || (tree.nodeType === Node.DOCUMENT_NODE ? tree.defaultView?.frameElement : null);
    const inBody = !tree.host && (element.ownerDocument?.body?.contains(element) ?? false);
    const root = inBody ? element.ownerDocument.body : element.closest('head');
    const parts = [];
    let anchored = false;   // the walk stopped at an id, which needs no root
    let node = element;
    while (node && node.nodeType === 1 && node !== root) {
        const parent = node.parentElement;
        let part = node.tagName.toLowerCase();
        if (node.id) {
            part += `#${CSS.escape(node.id)}`;
            parts.unshift(part);
            anchored = true;
            break;
        }
        if (!parent) { parts.unshift(part); break; }
        const siblings = [...parent.children].filter(x => x.tagName === node.tagName);
        if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(node) + 1})`;
        parts.unshift(part);
        node = parent;
    }
    // An id is document-unique, so the shortened path is already absolute.
    // Prefixing it with `body > ` asserted a parent-child relationship that
    // the dropped ancestors contradict: `body > p#para` for a `<p id=para>`
    // inside a `<div>` matches nothing, and `querySelector` says so.
    let own;
    if (anchored || tree.host) own = parts.join(' > ');
    else if (!parts.length) own = inBody ? 'body' : 'head';
    else own = `${inBody ? 'body' : 'head'} > ${parts.join(' > ')}`;
    return host ? `${locate(host)} >> ${own}` : own;
}"""


#: The page in reading order: every element of the flat tree, from the top
#: document's body wherever it is evaluated, each visited with the last heading
#: a reader passed before it. It enters a frame where the snapshot does, when the frame is shown and
#: the page can script it. `visit(element, heading, context)` returns true to
#: stop. `enter(frame, body, context)`, if given, returns the context for the
#: frame's document; the top document's is `[]`. A heading is recorded after
#: its own visit, so a heading is never the heading it sits under, and before
#: its children, so what is inside a heading sits under it.
READING_ORDER_JS = """(visit, enter = null) => {
    const flat = __FLAT_TREE__;
    const visible = __VISIBLE__;
    let heading = {text: '', level: 0};
    let stopped = false;
    const walk = (node, context) => {
        if (stopped || node.nodeType !== Node.ELEMENT_NODE) return;
        if (visit(node, heading, context)) { stopped = true; return; }
        if (node.matches('h1,h2,h3,h4,h5,h6')) {
            heading = {text: (node.innerText || '').trim(), level: Number(node.localName.slice(1))};
        }
        const body = flat.isFrame(node) && visible(node) ? flat.frameBody(node) : null;
        const inner = body && enter ? enter(node, body, context) : context;
        for (const child of [...flat.children(node, body)]) walk(child, inner);
    };
    // Evaluated in a frame (a locator inside one runs there), `document` is
    // that frame's, and a walk from its body saw none of the headings before
    // the frame. The top document is reachable from any frame the page can
    // script, and those are the only frames the walk enters (P16-16).
    let top = document;
    try { top = window.top.document; } catch {}
    walk(top.body, []);
}""".replace("__FLAT_TREE__", FLAT_TREE_JS).replace("__VISIBLE__", VISIBLE_JS)


#: Which heading an element sits under: the last one a reader passed before it.
#: Beside `LOCATOR_JS` and for the same reason: three stages report a heading
#: for what they found (harvested visuals, embedded walkthroughs, plain media),
#: and three copies of this walk are three chances to disagree about it. It was
#: the last heading in document order, which cannot see a heading in a shadow
#: root or a frame, and put an element in a slot under the headings around its
#: light-DOM position rather than where it renders (docs/09 P16-12). It walks
#: from the top document wherever it is evaluated (P16-16). An element outside
#: the reading order (in `<head>`,
#: an unslotted light child, a hidden frame) sits under no heading.
HEADING_JS = """element => {
    const inOrder = __READING_ORDER__;
    let found = {text: '', level: 0};
    inOrder((node, heading) => {
        if (node !== element) return false;
        found = heading;
        return true;
    });
    return found;
}""".replace("__READING_ORDER__", READING_ORDER_JS)


async def extract_semantic_content(
    page: Page, own_labels: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """Read the prepared page into records, links, metadata and the snapshot.

    `own_labels` maps each harvested asset's id to the `aria-label` the page
    gave the element, as the harvest read it before writing its own
    description there. Where the page wrote none, the attribute now holds
    WebShot's text, recognized text included, and a reader of the snapshot or
    of `embedded_media` must not be handed that as the page's (docs/09 P20-1).
    """
    extracted: dict[str, Any] = await page.evaluate(
        """({ownLabels}) => {
            const visible = __VISIBLE__;
            const locator = __LOCATOR__;
            const flat = __FLAT_TREE__;
            const owned = new Set(__HARVEST_MARKERS__);
            const mediaTags = ['audio', 'video', 'iframe', 'object', 'embed'];

            // The page as a reader sees it, which `document` alone is not. An
            // open shadow root's content and a same-origin frame's document
            // both render and print, and reach the PDF's text layer, yet
            // neither is in `document.querySelectorAll` or in
            // `document.body.innerHTML`. Every text surface built from the
            // snapshot lost them, with nothing in the manifest saying so
            // (docs/09 P16-6). So the walk follows the flat tree
            // (`FLAT_TREE_JS`). A host's light children that no slot shows
            // are not rendered, and the walk skips them. A frame the page
            // cannot script is recorded, not read, and Python names it in a
            // warning.
            //
            // One walk yields the records' elements and the working copy
            // together. The copy is built in an inert document (no browsing
            // context), because a live-document clone still loads resources:
            // rewriting an <img src> below would fire real requests for
            // bundle-relative paths that do not exist next to the page.
            //
            // Visibility is decided on the live node, where computed styles
            // exist, and recorded as a marker on its copy that the clone
            // filter removes. An element survives if it is visible itself,
            // keeps media alive regardless, or contains something that does
            // (visibility:hidden ancestors can have visible descendants).
            const keepTags = new Set(['AUDIO', 'VIDEO', 'TRACK', 'SOURCE', 'OBJECT', 'EMBED']);
            const inert = new DOMParser().parseFromString(
                '<!doctype html><html><body></body></html>', 'text/html');
            const clone = inert.body;
            const elements = [];        // the flat tree's elements, in reading order
            const kept = new WeakSet();
            const liveOf = new Map();   // a copied control or <svg> -> the live one it copies
            const unreadFrames = [];
            const readFrameAssets = []; // asset ids of frames whose document was read
            // A `display: contents` element has no box of its own, so
            // `visible()` is false for it whatever it holds, and an element
            // was kept only for a kept *element* inside it. Text sitting
            // directly in one was dropped: every <slot> is such an element,
            // and so is plenty of light-DOM layout (docs/09 P16-6). Its text
            // is shown when the text itself has a box and the element is not
            // `visibility: hidden`, which lays text out without drawing it.
            const rendered = text => {
                const range = text.ownerDocument.createRange();
                range.selectNodeContents(text);
                return [...range.getClientRects()].some(rect => rect.width > 0 && rect.height > 0);
            };
            const showsOwnText = element => {
                if (element.closest('[data-webshot-hidden="true"]')) return false;
                const style = getComputedStyle(element);
                return style.display === 'contents' && style.visibility !== 'hidden';
            };
            const copyOf = node => {
                if (node.nodeType === Node.TEXT_NODE) return inert.createTextNode(node.data);
                if (node.nodeType !== Node.ELEMENT_NODE) return null;
                elements.push(node);
                const shown = visible(node);
                const frame = flat.isFrame(node) && shown;
                const body = frame ? flat.frameBody(node) : null;
                // Ours in every tree the walk enters, because the harvest
                // cleaned them all first (`HARVEST_MARKERS`). Before it did, a
                // page-authored id on a frame inside a shadow root named
                // another asset's screenshot as that frame's (docs/09 P16-7).
                const assetId = node.getAttribute('data-webshot-asset-id') || '';
                if (frame && !body) unreadFrames.push({
                    locator: locator(node), title: node.title || '', assetId});
                if (body && assetId) readFrameAssets.push(assetId);
                // A frame that was read becomes a plain container: the HTML
                // parser reads an <iframe>'s content as text, so the frame's
                // document cannot stay inside one through the sanitizer.
                const copy = body ? inert.createElement('div') : inert.importNode(node, false);
                if (body) for (const name of ['title', 'data-webshot-asset-id']) {
                    if (node.hasAttribute(name)) copy.setAttribute(name, node.getAttribute(name));
                }
                // Outside the top document's own tree, only the harvest's
                // markers are ours: it removed the page's copies of those from
                // every tree it reached, and they carry the asset mapping for
                // the visuals it found there. Any other `data-webshot-*`
                // arriving from a shadow root or a frame is the page's.
                if (node.getRootNode() !== document) {
                    for (const {name} of [...copy.attributes]) {
                        if (name.startsWith('data-webshot-') && !owned.has(name)) {
                            copy.removeAttribute(name);
                        }
                    }
                }
                if (['INPUT', 'TEXTAREA', 'SELECT'].includes(node.tagName)) liveOf.set(copy, node);
                // An <svg>'s <title> is never rendered, so the filter below
                // removes it from the copy; its label is read off the live one.
                if (node.localName === 'svg') liveOf.set(copy, node);
                const ownText = !shown && showsOwnText(node);
                let keepsChild = false;
                for (const child of [...flat.children(node, body)]) {
                    const childCopy = copyOf(child);
                    if (!childCopy) continue;
                    copy.appendChild(childCopy);
                    keepsChild ||= kept.has(childCopy)
                        || (ownText && child.nodeType === Node.TEXT_NODE && rendered(child));
                }
                // A frame that was read is kept for what is in it, not for its
                // own box: an empty one (an `about:blank` player not yet
                // loaded) leaves no container behind.
                if ((shown && !body) || keepsChild || keepTags.has(node.tagName)) kept.add(copy);
                else copy.setAttribute('data-webshot-invisible', 'true');
                return copy;
            };
            for (const child of [...flat.children(document.body, null)]) {
                const childCopy = copyOf(child);
                if (childCopy) clone.appendChild(childCopy);
            }

            // The OCR suffix the visuals pass appends to alt and aria text is
            // for the rendered PDF's accessibility. Anywhere else it would be
            // recognized text, which depends on the machine that read it,
            // standing as text the page wrote (docling reads alt as a
            // caption). The OCR segment is always last in the description,
            // so a label is cut at the FIRST marker part: recognized text
            // that itself contains ' | ' must not leak fragments through a
            // keep-the-non-marker-parts filter.
            const ocrMarker = 'Text recognized within visual asset';
            const withoutOcr = value => {
                const parts = (value || '').split(' | ');
                const markerIndex = parts.findIndex(part => part.startsWith(ocrMarker));
                return (markerIndex >= 0 ? parts.slice(0, markerIndex) : parts).join(' | ');
            };
            // The page's own label for an element. The harvest wrote its
            // description into `aria-label` wherever the page had none, so for
            // a harvested element the attribute is read from `ownLabels`,
            // taken before that write. The marker cut is the fallback for an
            // element the caller passed no label for (docs/09 P20-1).
            const ownLabel = element => {
                const assetId = element.getAttribute('data-webshot-asset-id');
                if (assetId && Object.hasOwn(ownLabels, assetId)) return ownLabels[assetId];
                return withoutOcr(element.getAttribute('aria-label'));
            };

            const blocks = [];
            const blockSelector = 'h1,h2,h3,h4,h5,h6,p,blockquote,pre,ul,ol,dl,table,figure,input,textarea,select,audio,video,iframe,object,embed';
            for (const element of elements) {
                if (!element.matches(blockSelector)) continue;
                const tag = element.tagName.toLowerCase();
                if (!visible(element) && !mediaTags.includes(tag)) continue;
                if ((tag === 'p' && element.closest('li,table,blockquote,figure')) || ((tag === 'ul' || tag === 'ol') && element.parentElement?.closest('ul,ol'))) continue;
                const base = { id: `block-${String(blocks.length + 1).padStart(5, '0')}`, locator: locator(element), text: (element.innerText || '').trim() };
                if (/^h[1-6]$/.test(tag)) blocks.push({...base, type: 'heading', level: Number(tag.slice(1))});
                else if (tag === 'p') blocks.push({...base, type: 'paragraph'});
                else if (tag === 'blockquote') blocks.push({...base, type: 'blockquote'});
                else if (tag === 'pre') blocks.push({...base, type: 'code'});
                else if (tag === 'ul' || tag === 'ol') blocks.push({...base, type: 'list', ordered: tag === 'ol', items: [...element.children].filter(x => x.tagName === 'LI').map(x => (x.innerText || '').trim())});
                else if (tag === 'dl') blocks.push({...base, type: 'definitions', entries: [...element.children].reduce((out, x) => { if (x.tagName === 'DT') out.push({term: (x.innerText || '').trim(), definition: ''}); else if (x.tagName === 'DD' && out.length) out.at(-1).definition = (x.innerText || '').trim(); return out; }, [])});
                else if (tag === 'table') {
                    const rows = [...element.rows].map(row => [...row.cells].map(cell => ({text: (cell.innerText || '').trim(), header: cell.tagName === 'TH', colspan: cell.colSpan, rowspan: cell.rowSpan})));
                    blocks.push({...base, type: 'table', caption: element.caption?.innerText?.trim() || '', rows});
                } else if (tag === 'figure') blocks.push({...base, type: 'figure', caption: element.querySelector('figcaption')?.innerText?.trim() || '', assetIds: [...element.querySelectorAll('[data-webshot-asset-id]')].map(x => x.getAttribute('data-webshot-asset-id'))});
                else if (tag === 'input') blocks.push({...base, type: 'form-value', name: element.name || element.getAttribute('aria-label') || element.type, value: element.type === 'password' ? '[REDACTED PASSWORD]' : (element.type === 'checkbox' || element.type === 'radio' ? String(element.checked) : element.value)});
                else if (tag === 'textarea') blocks.push({...base, type: 'form-value', name: element.name || element.getAttribute('aria-label') || 'textarea', value: element.value});
                else if (tag === 'select') blocks.push({...base, type: 'form-value', name: element.name || element.getAttribute('aria-label') || 'select', value: [...element.selectedOptions].map(x => x.text).join(', ')});
                else if (mediaTags.includes(tag)) blocks.push({...base, type: 'embedded-media', mediaType: tag, sourceUrl: element.currentSrc || element.src || element.data || element.getAttribute('src') || '', title: element.title || ownLabel(element), tracks: [...element.querySelectorAll('track')].map(track => ({kind: track.kind, language: track.srclang, label: track.label, url: track.src}))});
            }
            const links = elements.filter(element => element.matches('a[href]')).filter(visible).map(a => ({text: (a.innerText || a.getAttribute('aria-label') || '').trim(), url: a.href}));
            const metadata = {
                title: document.title || '',
                language: document.documentElement.lang || '',
                description: document.querySelector('meta[name="description"]')?.content || '',
                author: document.querySelector('meta[name="author"]')?.content || '',
                keywords: document.querySelector('meta[name="keywords"]')?.content || '',
                canonicalUrl: document.querySelector('link[rel="canonical"]')?.href || '',
                publishedTime: document.querySelector('meta[property="article:published_time"], time[datetime]')?.content || document.querySelector('time[datetime]')?.dateTime || ''
            };
            const jsonLd = [...document.querySelectorAll('script[type="application/ld+json"]')].map(script => { try { return JSON.parse(script.textContent); } catch { return {parseError: true, raw: script.textContent}; } });

            // MathML is foreign content, and the sanitizer's HTML allowlist
            // drops a foreign element together with everything inside it: a
            // formula the PDF rendered was missing from content.html and from
            // every surface built on it, with nothing saying so (docs/09
            // P14-36). Each visible <math> becomes an HTML element holding
            // the formula as text -- its TeX annotation where the page
            // carries one (the author's own source, and the form an agent can
            // read), else its alttext, else its linear text -- marked with
            // what that text is. This runs before the invisible filter below
            // because an <annotation> is never rendered, so it is always
            // marked invisible, and removing it first would lose the source.
            //
            // The linear text is read only after that same filter has run
            // inside the formula. `textContent` reads every descendant, drawn
            // or not, so an <mphantom> (laid out, never painted) or a
            // `display: none` term reached content.html as part of the
            // formula when the page gave no TeX (docs/09 P18-4). One selector
            // serves both removals, so what a formula may say and what the
            // page may say cannot drift apart.
            const excluded = 'script,style,noscript,template,[data-webshot-hidden="true"],[data-webshot-ocr-text="true"],[data-webshot-invisible="true"]';
            for (const math of [...clone.querySelectorAll('math')]) {
                if (math.closest('[data-webshot-invisible="true"],[data-webshot-hidden="true"]')) continue;
                const tex = (math.querySelector('annotation[encoding="application/x-tex"]')?.textContent || '').trim();
                const alttext = (math.getAttribute('alttext') || '').trim();
                math.querySelectorAll(`annotation,annotation-xml,${excluded}`).forEach(x => x.remove());
                const linear = (math.textContent || '').replace(/\\s+/g, ' ').trim();
                const [source, text] = tex ? ['tex', tex] : alttext ? ['alttext', alttext] : ['text', linear];
                if (!text) { math.remove(); continue; }
                const holder = inert.createElement(math.getAttribute('display') === 'block' ? 'div' : 'span');
                holder.setAttribute('data-webshot-math', source);
                holder.textContent = text;
                math.replaceWith(holder);
            }
            clone.querySelectorAll(excluded).forEach(x => x.remove());
            // Live control state, from the live control each copy was made from.
            //
            // A copy carries content *attributes*, not IDL properties, so a
            // control the user or a script changed after load arrives with its
            // page-authored default. Reading `.value` off the copy reads that
            // same default back: the rewrite this replaced was a no-op, and
            // checkbox/radio checkedness and select choice were never carried
            // at all, so `content.html` and the `form-value` records above
            // disagreed about the same page (docs/09 P8-72).
            //
            // **Paired as the copy is made, not by position.** Position was
            // the first attempt and it is wrong: the filter above has already
            // removed hidden and excluded elements, so one dropped input shifts
            // every later index and controls receive each other's values — a
            // worse fidelity bug than the one being fixed. A mark written on
            // the live DOM and carried through serialization replaced it until
            // the walk above began building the copy node by node (P16-6).
            //
            // Passwords are still replaced by the marker rather than copied,
            // and that is decided by the *copy's* own type, so it holds for a
            // control with no live pair (spec §6.2).
            clone.querySelectorAll('input').forEach(element => {
                const live = liveOf.get(element);
                const type = (element.getAttribute('type') || 'text').toLowerCase();
                if (type === 'password') {
                    element.setAttribute('value', '[REDACTED PASSWORD]');
                } else if (type === 'checkbox' || type === 'radio') {
                    if (live ? live.checked : element.hasAttribute('checked')) {
                        element.setAttribute('checked', '');
                    } else {
                        element.removeAttribute('checked');
                    }
                } else {
                    element.setAttribute('value', (live ? live.value : element.value) || '');
                }
            });
            clone.querySelectorAll('textarea').forEach(element => {
                const live = liveOf.get(element);
                element.textContent = (live ? live.value : element.value) || '';
            });
            clone.querySelectorAll('select').forEach(element => {
                const live = liveOf.get(element);
                if (!live) return;
                const chosen = new Set([...live.selectedOptions].map(option => option.index));
                [...element.options].forEach((option, position) => {
                    if (chosen.has(position)) option.setAttribute('selected', '');
                    else option.removeAttribute('selected');
                });
            });
            // Harvested visuals point at their bundle copies: the snapshot is
            // standalone, and the bridge matches pictures to assets by these
            // URIs (docs/02: "asset refs rewritten to assets/").
            clone.querySelectorAll('[data-webshot-asset-id]').forEach(element => {
                const assetId = element.getAttribute('data-webshot-asset-id');
                const file = `assets/${assetId}.png`;
                if (element.tagName === 'IMG') {
                    element.setAttribute('src', file);
                    element.removeAttribute('srcset');
                    element.removeAttribute('sizes');
                } else if (element.tagName === 'SVG' || element.tagName === 'svg') {
                    const image = inert.createElement('img');
                    image.setAttribute('src', file);
                    image.setAttribute('data-webshot-asset-id', assetId);
                    // No text the page did not write: an <svg> it gave no
                    // label is an image with an empty alt. "Vector graphic"
                    // stood here and reached content.md and the chunks as a
                    // caption the page never had (docs/09 P20-1).
                    const title = liveOf.get(element)?.querySelector('title')?.textContent;
                    const label = ownLabel(element) || title || '';
                    image.setAttribute('alt', label.trim());
                    element.replaceWith(image);
                } else if (element.tagName === 'IFRAME' || element.tagName === 'FRAME') {
                    // Only a frame the walk could not read is still a frame
                    // here; one it read became a container of its document.
                    // Its screenshot is all the bundle holds of it, as for an
                    // <svg>, so it stands where the frame renders, as a
                    // picture. Left a frame, the sanitizer dropped it, and the
                    // text OCR found in it reached assets.json and no text
                    // surface (docs/09 P16-7).
                    const image = inert.createElement('img');
                    image.setAttribute('src', file);
                    image.setAttribute('data-webshot-asset-id', assetId);
                    const label = ownLabel(element) || element.getAttribute('title') || '';
                    image.setAttribute('alt', label.trim());
                    element.replaceWith(image);
                }
            });
            // A canvas the page gave no description is printed with WebShot's
            // own alt, which is not the page's text either.
            clone.querySelectorAll('img[data-webshot-rendered-canvas="placeholder"]')
                .forEach(element => element.setAttribute('alt', ''));
            for (const attribute of ['data-webshot-visual-index', 'data-webshot-background-asset', 'data-webshot-rendered-canvas', 'data-webshot-frame-index']) {
                clone.querySelectorAll(`[${attribute}]`).forEach(element => element.removeAttribute(attribute));
            }
            // The clone keeps only the human-authored part of each label.
            for (const element of clone.querySelectorAll('[data-webshot-asset-id]')) {
                for (const attribute of ['alt', 'aria-label', 'aria-description']) {
                    const value = element.getAttribute(attribute);
                    if (!value || !value.includes(ocrMarker)) continue;
                    const kept = withoutOcr(value);
                    if (kept) element.setAttribute(attribute, kept);
                    else element.removeAttribute(attribute);
                }
            }
            // A figure whose visual did not survive capture (asset cap,
            // unrasterized canvas) has no image in the record: what remains
            // of it IS ordinary text, so its caption is kept as such rather
            // than as a figure the bundle cannot illustrate — and the
            // extraction backend would otherwise drop it whole, caption and
            // all.
            for (const figure of [...clone.querySelectorAll('figure')]) {
                if (!figure.querySelector('img')) figure.replaceWith(...figure.childNodes);
            }
            return {metadata, blocks, links, jsonLd, text: (document.body.innerText || '').trim(), bodyHtml: clone.innerHTML, unreadFrames, readFrameAssets};
        }""".replace("__LOCATOR__", LOCATOR_JS)
        .replace("__VISIBLE__", VISIBLE_JS)
        .replace("__FLAT_TREE__", FLAT_TREE_JS)
        .replace("__HARVEST_MARKERS__", json.dumps(HARVEST_MARKERS)),
        {"ownLabels": dict(own_labels or {})},
    )
    extracted["html"] = standalone_document(
        extracted["metadata"]["title"],
        extracted["metadata"]["language"],
        sanitize_body_html(extracted.pop("bodyHtml")),
    )
    extracted["closedShadowHosts"] = await _closed_shadow_hosts(page)
    return extracted


#: Where a reader of the bundle would look for the words, named in each warning
#: below so the sentence says which files lack them.
_TEXT_SURFACES = (
    "content.html, nor in the content.md, content.txt and chunks.jsonl built from it"
)

#: Elements whose text nodes are not text the page shows.
_NOT_SHOWN = frozenset({"STYLE", "SCRIPT", "TEMPLATE", "NOSCRIPT"})


def _holds_text(root: Mapping[str, Any]) -> bool:
    """Whether a DevTools DOM subtree has a text node with something in it.

    Nested shadow roots count: behind a closed root, an open one is out of
    reach too. Iterative, because a page's depth is not Python's to bound.
    """
    stack = [root]
    while stack:
        node = stack.pop()
        if node.get("nodeType") == 3 and str(node.get("nodeValue", "")).strip():
            return True
        if node.get("nodeName") in _NOT_SHOWN:
            continue
        stack.extend(node.get("children", ()))
        stack.extend(node.get("shadowRoots", ()))
    return False


def _closed_hosts(document: Mapping[str, Any]) -> Iterator[int]:
    """Backend ids of the hosts of closed shadow roots that hold text, in order."""
    stack = [document]
    while stack:
        node = stack.pop()
        roots = node.get("shadowRoots", ())
        if any(r.get("shadowRootType") == "closed" and _holds_text(r) for r in roots):
            yield int(node["backendNodeId"])
        following = [*roots, *node.get("children", ())]
        if "contentDocument" in node:
            following.append(node["contentDocument"])
        stack.extend(reversed(following))


#: The handles the closed-root check resolves, released together when it ends.
_OBJECT_GROUP = "webshot-closed-shadow-roots"


async def _closed_shadow_hosts(page: Page) -> list[str] | None:
    """Locators of the elements whose closed shadow root holds text.

    No page script can see into a closed shadow root, or even find one: the
    host's `shadowRoot` is null, exactly as for an element with none. Chromium
    renders and prints it all the same, so the PDF holds text the snapshot
    cannot, and the one place left to notice is outside the page. The DevTools
    protocol's pierced DOM reports each root's type (docs/09 P16-6).

    A host inside a frame the page cannot script is skipped: the frame's own
    warning already says none of its text was read, and no locator from the
    top document reaches it. `None` means the check could not run, which the
    caller reports: a check that did not run has not found nothing.
    """
    try:
        session = await devtools(page)
        try:
            pierced = await session.send(
                "DOM.getDocument", {"depth": -1, "pierce": True}
            )
            locators: list[str] = []
            for backend_id in _closed_hosts(pierced["root"]):
                resolved = await session.send(
                    "DOM.resolveNode",
                    {"backendNodeId": backend_id, "objectGroup": _OBJECT_GROUP},
                )
                located = await session.send(
                    "Runtime.callFunctionOn",
                    {
                        "objectId": resolved["object"]["objectId"],
                        "functionDeclaration": "function () {"
                        " if (window.top !== window && !window.frameElement) return null;"
                        f" return ({LOCATOR_JS})(this); }}",
                        "returnByValue": True,
                    },
                )
                # A script error is a check that failed, not a host to skip.
                if "exceptionDetails" in located:
                    return None
                value = located["result"].get("value")
                if isinstance(value, str):
                    locators.append(value)
            return locators
        finally:
            # The session stays (see `devtools`), so what this enabled goes:
            # a DOM agent left on reports every later mutation to it.
            await session.send(
                "Runtime.releaseObjectGroup", {"objectGroup": _OBJECT_GROUP}
            )
            await session.send("DOM.disable")
    except (PlaywrightError, KeyError):
        return None


def with_frame_content(
    extracted: Mapping[str, Any], assets: Sequence[VisualAsset]
) -> list[VisualAsset]:
    """The assets, each iframe's `frame_content` set from what the walk did.

    docs/04-spec.md §5.6: a frame the page can script is read into the
    snapshot, and one it cannot is captured visually. The harvest cannot tell
    which, because it runs first, so it records `screenshot`: all it had taken.
    A frame whose document the walk read becomes `dom` here. Any other keeps
    `screenshot`, whatever kept the walk out, because its screenshot is then
    all the bundle holds of it (docs/09 P16-7).
    """
    read = set(extracted["readFrameAssets"])
    return [
        replace(asset, frame_content="dom")
        if asset.kind == "iframe" and asset.id in read
        else asset
        for asset in assets
    ]


def unread_text_warnings(
    extracted: Mapping[str, Any],
    assets: Sequence[VisualAsset],
    *,
    pictured: Collection[str],
) -> list[str]:
    """What the page shows that the snapshot could not read, said by name.

    CONTRIBUTING rule 5: the PDF prints a cross-origin frame and a closed
    shadow root, and the bundle's text lacks both, so the manifest says so.
    Each frame is named with what the bundle does hold of it, if anything: its
    screenshot, and the text OCR found there (docs/04-spec.md §5.6).

    `pictured` is the asset ids the extraction attached to a picture. A frame
    captured as an asset stands in the snapshot as its screenshot, so the text
    OCR found there reaches every text surface, unless the attachment was
    skipped. Then the extraction's own warning says why, and this one must not
    say the text arrived (docs/09 P16-7).
    """
    warnings: list[str] = []
    by_id = {asset.id: asset for asset in assets}
    for frame in extracted["unreadFrames"]:
        title = f' ("{frame["title"]}")' if frame["title"] else ""
        asset = by_id.get(frame["assetId"])
        if asset is None:
            held = (
                f"Its text is in the PDF but not in {_TEXT_SURFACES}. It was not "
                "captured as a visual asset either."
            )
        elif not asset.ocr.text:
            # Not "no text was recognized": with OCR off nothing looked, and
            # the result does not say which (Tesseract leaves `engine` empty on
            # an empty read; RapidOCR sets it).
            held = (
                f"Its text is in the PDF but not in {_TEXT_SURFACES}. Its "
                f"screenshot is {asset.id}; assets.json holds no recognized "
                "text for it."
            )
        elif asset.id in pictured:
            held = (
                f"Its text is in the PDF. content.html holds its screenshot, "
                f"{asset.id}, in its place, and the content.md, content.txt and "
                "chunks.jsonl built from it hold the text OCR recognized there, "
                "not the frame's own."
            )
        else:
            held = (
                f"Its text is in the PDF but not in {_TEXT_SURFACES}. Text "
                f"recognized in its screenshot, {asset.id}, is in assets.json only."
            )
        warnings.append(
            f"The frame at `{frame['locator']}`{title} could not be read. Its "
            "document is cross-origin or sandboxed, and no script in the page can "
            f"reach it. {held}"
        )
    hosts = extracted["closedShadowHosts"]
    if hosts is None:
        warnings.append(
            "Closed shadow roots could not be checked for text. Text inside one "
            f"is in the PDF and not in {_TEXT_SURFACES}, and no other warning "
            "would say so."
        )
    elif hosts:
        named = ", ".join(f"`{host}`" for host in hosts)
        warnings.append(
            f"{len(hosts)} closed shadow root(s) hold text, on {named}. No script "
            "can read a closed shadow root, so that text is in the PDF wherever "
            f"it is visible, and not in {_TEXT_SURFACES}."
        )
    return warnings


#: Every password value in one frame's document and in each open shadow root
#: inside it. Playwright's aria snapshot reads both, and reads every frame, so
#: asking only the top document let a password typed into a shadow root or a
#: frame through to `accessibility.yaml` (docs/09 P16-6).
_PASSWORD_VALUES_JS = """() => {
    const values = [];
    const visit = root => {
        for (const element of root.querySelectorAll('*')) {
            if (element.localName === 'input' && element.type === 'password' && element.value) {
                values.push(element.value);
            }
            if (element.shadowRoot) visit(element.shadowRoot);
        }
    };
    visit(document);
    return values;
}"""


async def _redact_password_values(page: Page, text: str) -> str:
    """Remove every password input's value from a live-DOM-derived artifact.

    The block/clone extraction above redacts as it reads, but Playwright's aria
    snapshot reads the live DOM itself and reports textbox values — which is how
    a password reached `accessibility.yaml` (docs/04-spec.md §6.2; caught by the
    form-fields fixture in Phase 2).  The values are pulled once, used only to
    scrub, and never leave this function.  Each is scrubbed both raw and in its
    escaped-quoted spelling, because a snapshot may quote a value it reports.

    **Longest first, and that is not a tidiness preference.**  Replacement is
    sequential over a shared string, so a shorter password that is a prefix of
    a longer one destroys the longer one's only chance to match: a sign-in form
    holding `secret` in the current-password field and `secret123` in the new
    one rewrote the second to `[REDACTED PASSWORD]123`, and the pass for
    `secret123` then found nothing to do — publishing the suffix of a password
    into `accessibility.yaml`.  Ordering by descending length makes every value
    meet the text before anything that could be contained in it, which is the
    condition under which sequential replacement is equivalent to simultaneous
    replacement here.  Spec §6.2 is a MUST, so this is not a partial-leak
    tradeoff to accept (docs/09 P8-8).

    Every frame is asked, and each open shadow root in it, because the aria
    snapshot reports both (docs/09 P16-6). A frame that cannot be asked raises,
    and the caller withholds the snapshot.
    """
    values: list[str] = []
    for frame in page.frames:
        values.extend(await frame.evaluate(_PASSWORD_VALUES_JS))
    spellings: list[str] = []
    for value in values:
        quoted = value.replace("\\", "\\\\").replace('"', '\\"')
        spellings.append(value)
        if quoted != value:
            spellings.append(quoted)
    for spelling in sorted(set(spellings), key=len, reverse=True):
        text = text.replace(spelling, PASSWORD_REDACTION)
    return text


async def _accessibility_snapshot(page: Page) -> tuple[str, str | None]:
    method = getattr(page.locator("body"), "aria_snapshot", None)
    if not method:
        return "", "Accessibility snapshot requires Playwright 1.49 or newer."
    try:
        try:
            snapshot = await method(mode="ai")
        except TypeError:
            snapshot = await method()
    except PlaywrightError as exc:
        return "", f"Accessibility snapshot failed: {exc}"
    # The snapshot reads every frame. One whose password fields cannot be read
    # cannot have them redacted, so the snapshot is withheld rather than
    # published with whatever that frame held (spec §6.2).
    try:
        return await _redact_password_values(page, snapshot), None
    except PlaywrightError as exc:
        return "", (
            "Accessibility snapshot withheld: a frame's password fields could "
            f"not be read, so their values could not be redacted: {exc}"
        )
