# Protected-viewer DOM fixtures

These two documents reproduce the **accessibility-tree contract** that
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
| `sharepoint-portrait.html` | 3 tall pages in a scrolling container | page geometry, inter-page step, page count, segmented capture |
| `sharepoint-landscape.html` | 2 wide slides plus the toolbar | the landscape branch's zoom, paging, and page-number controls |
