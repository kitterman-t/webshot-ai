"""Take a video's controls out of the spinner they show for a moment after load.

Chromium's controls for a `<video controls>` show a loading spinner whenever the
element is loading its metadata, and every such element passes through that
state as the page loads: resource selection sets the network state to LOADING
before it reads `preload`, and the controls see that at once. With
`preload="none"` the load is deferred in the same step and the state goes to
IDLE, but the spinner does not go with it. It stays until its current turn
ends, which is 1,333 ms after it started, and goes only when a rendered frame
delivers `animationend`. So for about 1.4 s after the page loads on an idle
machine, and longer on one whose renderer is starved, every such video is drawn
with a spinner at an angle that depends on the clock (docs/09 P66).

A capture photographs a page's visuals once `--delay` (1 s) and the lazy-load
pass have run after the page loaded, which is close enough to that window for
the machine's speed and load to decide which side of it a capture falls on, and
without a bundle it prints on the same timeline. So a video's asset showed idle
controls or a spinner, at one of many angles, and its printed page could too.
`animations="disabled"` cannot stop it: Playwright finds animations through
`getAnimations()` on the document and its open shadow roots, and the controls
live in the element's user-agent shadow root, which no script reaches.

What does reach them is taking the element out of the document: its controls
are hidden, which ends the spinner at once, and when it is put back where it
was they are shown in the state the element is in now, which for a video that
is not loading has no spinner. Removing and restoring `controls` would do the
same, but it moves the attribute to the end of the element's list, and the
bundle's `content.html` writes attributes in the order the element has them.
Nothing is fetched: an element that has already selected its source does not
select it again when it is reinserted. The page can see it happen, as two
`childList` records for a `MutationObserver`.
"""

from __future__ import annotations

import logging

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

from .snapshot import FLAT_TREE_JS

LOGGER = logging.getLogger("webshot")

#: Take one video with native controls out and put it back where it was,
#: unless it is one of the four kinds left alone; true if it was moved. Both
#: scripts below use it, so the stage and the harvest leave alone the same
#: videos.
SETTLE_ONE_JS = """video => {
    if (!video.controls) return false;
    // Playing, its controls fade out on a timer, and showing them
    // again would start the fade over.
    if (!video.paused) return false;
    // No source selected yet: reinserting it would start the load.
    if (video.networkState === HTMLMediaElement.NETWORK_EMPTY) return false;
    // Loading its metadata, it shows the spinner because that is its
    // state, and this would only start the turn again.
    if (video.networkState === HTMLMediaElement.NETWORK_LOADING
            && video.readyState === HTMLMediaElement.HAVE_NOTHING) return false;
    // Taking it out would take the focus with it, and its ring.
    if (video.matches(':focus-within')) return false;
    const parent = video.parentNode;
    const next = video.nextSibling;
    video.remove();
    parent.insertBefore(video, next);
    return true;
}"""

#: Returns how many videos were taken out and put back.
SETTLE_SCRIPT = """() => {
    const settle = __SETTLE_ONE__;
    const roots = [document];
    for (let index = 0; index < roots.length; index += 1) {
        for (const element of roots[index].querySelectorAll('*')) {
            if (element.shadowRoot) roots.push(element.shadowRoot);
        }
    }
    let settled = 0;
    for (const root of roots) {
        for (const video of root.querySelectorAll('video[controls]')) {
            if (settle(video)) settled += 1;
        }
    }
    return settled;
}""".replace("__SETTLE_ONE__", SETTLE_ONE_JS)

#: Settle every video a screenshot of `element` will show, the element itself
#: and any inside it, and return whether the element is itself a video whose
#: controls are in a loading state, which keeps its spinner (docs/09 P67).
#:
#: The harvest runs this just before each screenshot. The stage above runs
#: once, at the end of the prepare stage, and a load can end after it: a
#: `preload="metadata"` request that answers or fails later starts the
#: spinner's last turn then (P66-4). Inside, because a frame or a background
#: asset holding a player is photographed before the player's own turn comes,
#: if it comes at all: a frame's document, a shadow root and the frames within
#: them, as `FLAT_TREE_JS` walks them.
#:
#: The loading test is Chromium's own (`IsInLoadingState` in the loading
#: panel): loading with nothing yet, or playing with too little to go on.
SETTLE_INSIDE_SCRIPT = """element => {
    const flat = __FLAT_TREE__;
    const settle = __SETTLE_ONE__;
    const roots = [element];
    if (element.shadowRoot) roots.push(element.shadowRoot);
    if (flat.isFrame(element)) {
        try { if (element.contentDocument) roots.push(element.contentDocument); } catch {}
    }
    const videos = roots.flatMap(root => flat.trees(root))
        .flatMap(tree => [...tree.querySelectorAll('video')]);
    if (element.localName === 'video') videos.push(element);
    for (const video of videos) settle(video);
    return element.localName === 'video' && element.controls
        && element.networkState === HTMLMediaElement.NETWORK_LOADING
        && (element.readyState === HTMLMediaElement.HAVE_NOTHING
            || (!element.paused && element.readyState < HTMLMediaElement.HAVE_ENOUGH_DATA));
}""".replace("__FLAT_TREE__", FLAT_TREE_JS).replace("__SETTLE_ONE__", SETTLE_ONE_JS)


async def settle_media_controls(page: Page) -> int:
    """End the post-load spinner on every paused video's controls; count them.

    Run at the end of the prepare stage, so the bundle's screenshots and the
    print both see the controls as they stand once the spinner is over. Every
    frame is visited, cross-origin
    ones included, and in each the document and its open shadow roots. A frame
    that cannot be scripted is skipped: what it costs is a spinner in that
    frame's picture, not anything the bundle or the PDF says.
    """
    settled = 0
    for frame in page.frames:
        try:
            settled += await frame.evaluate(SETTLE_SCRIPT)
        except PlaywrightError as exc:
            LOGGER.debug(
                "Could not settle the video controls in frame %s: %s", frame.url, exc
            )
    return settled
