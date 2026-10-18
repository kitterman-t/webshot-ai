# Protected-viewer DOM fixtures

These documents reproduce the **accessibility-tree contract** that
`src/webshot/protected/viewer.py` depends on when it captures an authenticated
Microsoft SharePoint PDF viewer: the scrollable container labelled with the file
name, one absolutely positioned element per rendered page labelled `Page N.`, the
toolbar's `Page N of M` counter, and the toolbar controls the landscape path
drives by accessible name.

## Provenance, stated plainly

They were **authored from that contract**, not exported from a live tenant.
Two reasons, and the honest one is first:

1. Capturing a real viewer needs an authenticated SharePoint session, which the
   environment this phase was built in does not have (recorded as docs/09 P1-6).
2. A real export would have to be redacted before it could be committed, and a
   redaction mistake in a capture tool's own repository is exactly the failure
   docs/04-spec.md §6 exists to prevent. There is no tenant name, no document
   content, no user identity, and no URL in these files because none was ever
   in them.

## What the fixtures can and cannot catch

They catch **drift on WebShot's side**: if someone edits a selector, a regular
expression, or a toolbar name in `viewer.py`, `tests/test_viewer_dom.py` fails.
That is the regression these files exist to prevent, and it is the one that used
to be undetectable.

The portrait pages also draw colour bands, no two alike, so
`tests/test_protected_viewer.py` can run the segmented capture on them and
check every stitched page band for band. A segment shot from anywhere but
where its clip assumes puts a band out of place (docs/09 P42).

Every page element is in the DOM from the start, in every fixture. That is
how they were authored, not something observed: whether the live viewer
keeps pages far from the view in the DOM is not recorded. The capture does
not depend on it. It finds each page by its own `Page N.` label and scrolls
to where the page begins when its element is missing, and a test runs it
with a script that keeps only the pages in view (docs/09 P46).

`sharepoint-landscape-mixed.html` is the one fixture whose toolbar works,
so the landscape capture can be driven end to end: previous and next move
the current page, the page box and the `Page N of M` counter follow it, and
the zoom menu switches between "Window size" and 75%. That behaviour is
authored too. Its viewer puts the current slide's top at the top of the
view, and draws 75% by scaling each slide about its top centre without
moving it, so a script that recorded where each slide sits stays right
after the zoom changes. The capture assumes neither: it measures each slide
after the toolbar has gone to it (docs/09 P51).

They cannot catch **drift on Microsoft's side**. If the viewer's accessibility
tree changes, these fixtures keep passing while real captures break. Only a run
against a live tenant tells you that, and it stays a maintainer task —
re-capture, re-redact, replace these files, and update `SUPPORTED_VIEWERS` in
`viewer.py`.

## Supported viewer builds

`webshot.protected.viewer.SUPPORTED_VIEWERS` is the list, and the test asserts
it is not empty so the note cannot quietly disappear. As of Phase 1:

| Viewer | Contract observed | Notes |
|---|---|---|
| Microsoft SharePoint Online PDF viewer | 2025-08 – 2026-08 | Portrait documents scroll; landscape decks are paged through the toolbar at 75% zoom |

## The files

| File | Shape | Exercises |
|---|---|---|
| `sharepoint-portrait.html` | 3 tall pages in a scrolling container, each drawn as eleven 96px colour bands | page geometry, inter-page step, page count, segmented capture and stitching |
| `sharepoint-portrait-smooth.html` | the same, with `scroll-behavior: smooth` on the container | every segment shot where the viewer is, not where a scroll animation has got to (docs/09 P42) |
| `sharepoint-portrait-mixed.html` | 5 pages of different sizes, each drawn as colour bands: Letter, Letter landscape, Legal, a taller appended form, and a short, narrow last page the viewer cannot scroll to its top | each page found, measured and shot as itself, one shot or two as its own height needs (docs/09 P46) |
| `sharepoint-landscape.html` | 2 wide slides plus the toolbar | the landscape branch's zoom, paging, and page-number controls |
| `sharepoint-landscape-mixed.html` | 3 slides of different sizes, each drawn as colour bands: 16:9, 4:3, and a portrait page taller than the first, plus a toolbar that works | each slide found, measured and shot as itself at 75%, and the orientation read from page 1 (docs/09 P51) |
