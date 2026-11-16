"""A video site's watch page, printed for reading (docs/09 P120-6).

Two watch pages captured with the defaults printed the site's bar ("Skip
navigation", "Sign in") over a black, unloaded player, a rail of related
videos whose thumbnails printed blank, and a date only as "2y ago". Nothing on
the printed pages said when the video was published or when it was captured,
and the text layer held about a quarter of the bundle's text, most of the rest
being the related rail's titles.

In clean mode a watch page is printed without the bar, the player and the
related rail, which are the site's and not the video's, and the description
is opened with the rest of the page's closed regions (`collapsed.py`). The
footer of every page then says when the video was published and by whom, as
the page itself declares it (`discovered_metadata`), and the day it was
captured. In faithful mode nothing here applies: the page prints as it was
shown.

The elements are named by the site's own custom elements and ids, and only on
a watch page's address, so no other page's `#player` or `#secondary` is
touched.
"""

from __future__ import annotations

from datetime import date
from urllib.parse import parse_qs, urlsplit

from .discover import DiscoveredMetadata

#: The hosts a watch page is served from.
WATCH_HOSTS = frozenset({"www.youtube.com", "youtube.com", "m.youtube.com"})

#: What clean mode hides on a watch page: the site's bar, which holds "Skip
#: navigation" and "Sign in" and is fixed to the window; the player and the
#: boxes that hold it, which print black without a loaded video; and the rail
#: of related videos, wherever the layout puts it, and the comments.
WATCH_PAGE_NOISE: tuple[str, ...] = (
    "ytd-masthead",
    "#masthead-container",
    "#player",
    "#full-bleed-container",
    "ytd-player",
    "#secondary",
    "ytd-watch-next-secondary-results-renderer",
    "ytd-comments",
)


def is_watch_page(url: str) -> bool:
    """Whether `url` is a video's watch page: `/watch?v=…` on the site."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or parts.hostname not in WATCH_HOSTS:
        return False
    return parts.path == "/watch" and bool(parse_qs(parts.query).get("v"))


def watch_page_note(discovered: DiscoveredMetadata, captured: date) -> str:
    """The footer's line: when the video was published, by whom, and the capture.

    The date and the channel are what the page declares (its schema.org
    `uploadDate` and `author`, read into `discovered_metadata`); a value the
    page did not declare is left out rather than guessed.
    """
    published = ""
    if discovered.date and discovered.author:
        published = f"Published {discovered.date} by {discovered.author}. "
    elif discovered.date:
        published = f"Published {discovered.date}. "
    elif discovered.author:
        published = f"By {discovered.author}. "
    return f"{published}Captured {captured.isoformat()}."
