"""The one module that imports Trafilatura (CONTRIBUTING rule 1).

Two questions get asked of the captured, post-JS HTML, and Trafilatura is
better at both than the heuristics v2 had:

**Where is the main content?**  Trafilatura reports the *text* of the article,
not a CSS selector, so `capture/prepare.py` locates that text back in the live
DOM and isolates the element that holds it.  The v2 static selector list stays
as the fallback, and the manifest records which one won — a capture that
silently changed how it chose its content region would be unauditable.

**What does the page say about itself?**  The title, date, author and site
name the page declares.  This runs on every capture, and it is strictly
additive: nothing WebShot already recorded changes shape because of it.  None
of the four is Trafilatura's, so only the first question is still asked of it.

The date is not Trafilatura's (docs/09 P82).  Trafilatura gets it from
htmldate, and htmldate guesses.  With `extensive=True`, the default, it gave
"2026-01-01" to a document with no date anywhere in it (P3-3).  With
`extensive=False` it still reads a date from the URL's path ahead of the
page's `<meta>`, from a heading ahead of a `<time>`, and from a copyright
year, and since htmldate 1.11 from any `YYYY-MM-DD` in the text.  It drops a
declared date later than today, so its answer can change with the calendar,
and the order of its cues changes with its releases.  A capture tool may not
invent provenance, so `declared_date` reads the date from what the page
declares about itself, and Trafilatura's is never recorded.

The author and the site name are not Trafilatura's either (docs/09 P88).
When a page declares no author, Trafilatura takes one from the text of an
element classed or labelled as a byline; when it declares no site name, it
takes a part of the `<title>`, or the URL's host.  And it rewrites the names
it does read: it splits them at commas and "and", drops what follows a
digit, title-cases a lowercase one.  `declared_author` and
`declared_sitename` read each from the page's own declarations, as written.

A declaration can still name the wrong person: a law firm's article declared
the colleague who posted it, in `<meta name="author">` and in JSON-LD, and
printed its writer under an "Author(s)" heading (docs/09 P124-3).  So
`printed_byline` reads the names a page prints as its byline, by rules that
are this module's and narrow enough to refuse what P88 measured Trafilatura
getting wrong, and the byline replaces a declaration when it names someone
the declaration does not.

Nor is the title (docs/09 P93).  When a page has one `<h1>`, Trafilatura
takes its text ahead of the `<title>`; with none it can take one side of a
"Title | Site" `<title>`.  `declared_title` reads the title the page
declares, and with no title `<meta>` or JSON-LD that is the whole of its
`<title>`, as written.
"""

from __future__ import annotations

import datetime
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import SplitResult, urljoin, urlsplit

from trafilatura import extract

LOGGER = logging.getLogger("webshot")

# How strongly a declaration names the page's own date.  The first readable
# declaration of the strongest kind present is the date, so a page's stated
# publication beats its stated modification, and both beat a <time> that only
# marks a date somewhere in it.
_ORIGINAL, _MODIFIED, _MARKED = range(3)

#: The `<meta>` keys read as a date, by the `property`, `name`, `itemprop` or
#: `http-equiv` that carries them, lowercased, with `dc:` and `dcterms:`
#: spelled `dc.` and `dcterms.`.  A key not in this table is not read: the
#: table is the contract, not a sample of it.
_META_DATE_KEYS = {
    # Open Graph (ogp.me's article type, and the og:updated_time that
    # Facebook's crawler reads).
    "article:published_time": _ORIGINAL,
    "article:modified_time": _MODIFIED,
    "og:updated_time": _MODIFIED,
    # schema.org as microdata on a <meta>.
    "datepublished": _ORIGINAL,
    "datecreated": _ORIGINAL,
    "datemodified": _MODIFIED,
    # Dublin Core in HTML, DCMI's DC.* and DCTERMS.* names.
    "dc.date": _ORIGINAL,
    "dc.date.issued": _ORIGINAL,
    "dc.date.created": _ORIGINAL,
    "dc.date.modified": _MODIFIED,
    "dcterms.date": _ORIGINAL,
    "dcterms.issued": _ORIGINAL,
    "dcterms.created": _ORIGINAL,
    "dcterms.modified": _MODIFIED,
    # Highwire Press tags, which Google Scholar reads.
    "citation_publication_date": _ORIGINAL,
    "citation_date": _ORIGINAL,
    "citation_online_date": _ORIGINAL,
    # The plain names, and the HTTP header a page can restate.
    "date": _ORIGINAL,
    "pubdate": _ORIGINAL,
    "last-modified": _MODIFIED,
}

#: schema.org's three dates, as a JSON-LD key or a microdata `itemprop`.
_SCHEMA_ORG_DATES = {
    "datePublished": _ORIGINAL,
    "dateCreated": _ORIGINAL,
    "dateModified": _MODIFIED,
}
_ITEMPROP_DATES = {key.lower(): tier for key, tier in _SCHEMA_ORG_DATES.items()}

#: A calendar date no reader can take two ways: four-digit year, month, day,
#: as ISO 8601 writes it (`2024-03-05`, `20240305`) or with the slashes
#: Highwire's tags use (`2024/03/05`), at the start of the value and not run
#: into more digits.  `05/03/2024` is May or March depending on who reads it,
#: and `March 5, 2024` needs a parser that guesses at languages; neither is
#: read.  What follows the date (a time, a zone) is ignored, so the date is
#: the one the page wrote, in its own offset.
_DECLARED_DATE = re.compile(r"(\d{4})([-/]?)(\d{2})\2(\d{2})(?!\d)")

#: The kinds of declaration an author is read from, in the order they are
#: preferred.  The first kind the page uses is the author, every name of that
#: kind and none of the others': two kinds often name the same people in two
#: spellings ("John Jumper" in JSON-LD, "Jumper, John" in `citation_author`
#: and `DC.creator`, on one journal's article), and joining them would
#: double the authors.  Structured data comes first because it names each
#: author on their own; `<meta name="author">` is one free-form string.
(
    _JSON_LD_AUTHOR,
    _MICRODATA_AUTHOR,
    _META_AUTHOR,
    _CITATION_AUTHOR,
    _ARTICLE_AUTHOR,
    _DC_CREATOR,
) = range(6)

#: The `<meta>` keys read as an author, by the `name` or `property` that
#: carries them, spelled as `_meta_key` spells them.  As with the dates, the
#: table is the contract: `twitter:creator`, which names an account, is not
#: in it.
_META_AUTHOR_KEYS = {
    # HTML's own name: "the name of one of the page's authors".
    "author": _META_AUTHOR,
    # Highwire Press, one tag per author, which Google Scholar reads.
    "citation_author": _CITATION_AUTHOR,
    # Open Graph's article type.
    "article:author": _ARTICLE_AUTHOR,
    # Dublin Core in HTML.
    "dc.creator": _DC_CREATOR,
    "dcterms.creator": _DC_CREATOR,
}

#: The kinds of declaration a site name is read from, in the order they are
#: preferred.  A page has one site name, so the first value of the first
#: kind the page uses is it.
_OG_SITE_NAME, _JSON_LD_WEBSITE, _JSON_LD_PUBLISHER, _APPLICATION_NAME = range(4)

_META_SITENAME_KEYS = {
    # Open Graph: "the name of the overall site" the page is part of.
    "og:site_name": _OG_SITE_NAME,
    # HTML's own name for the web application the page belongs to.
    "application-name": _APPLICATION_NAME,
}

#: The kinds of declaration a title is read from, in the order they are
#: preferred.  A page has one title, so the first value of the first kind the
#: page uses is it.  The `<title>` comes last: of 41 real pages that also
#: declare a title in a `<meta>`, 18 wrote that title into the `<title>` with
#: a separator and the site's or a section's name after it (docs/09 P93).
(
    _OG_TITLE,
    _TWITTER_TITLE,
    _JSON_LD_TITLE,
    _CITATION_TITLE,
    _DC_TITLE,
    _TITLE_ELEMENT,
) = range(6)

#: The `<meta>` keys read as a title, by the `name` or `property` that
#: carries them, spelled as `_meta_key` spells them.  `<meta name="title">`
#: and the vendors' keys (`parsely-title`, `sailthru.title`) are not in it.
_META_TITLE_KEYS = {
    # Open Graph: the title of the page as a shared link shows it.
    "og:title": _OG_TITLE,
    # X's (Twitter's) cards: the title of the content a card shows.
    "twitter:title": _TWITTER_TITLE,
    # Highwire Press, which Google Scholar reads.
    "citation_title": _CITATION_TITLE,
    # Dublin Core in HTML.
    "dc.title": _DC_TITLE,
    "dcterms.title": _DC_TITLE,
}

#: Article and every type schema.org defines beneath it: what
#: `declared_article_titles` reads as an article the page declares.
_JSON_LD_ARTICLE_TYPES = frozenset(
    (
        "Article AdvertiserContentArticle NewsArticle AnalysisNewsArticle "
        "AskPublicNewsArticle BackgroundNewsArticle OpinionNewsArticle "
        "ReportageNewsArticle ReviewNewsArticle Report SatiricalArticle "
        "ScholarlyArticle MedicalScholarlyArticle SocialMediaPosting "
        "BlogPosting LiveBlogPosting DiscussionForumPosting TechArticle "
        "APIReference"
    ).split()
)

#: The schema.org types whose `name`, or else `headline`, is a title: Article
#: and WebPage, and every type schema.org defines beneath them.  A Product's,
#: an Event's or an Organization's name names the thing, not the page.
_JSON_LD_TITLED_TYPES = _JSON_LD_ARTICLE_TYPES | frozenset(
    (
        "WebPage AboutPage CheckoutPage CollectionPage MediaGallery "
        "ImageGallery VideoGallery ContactPage FAQPage ItemPage "
        "MedicalWebPage ProfilePage QAPage RealEstateListing SearchResultsPage"
    ).split()
)

#: Elements whose content is not HTML: a `<title>` inside one is an SVG or a
#: MathML element (an icon's label, on many pages), not the document's title.
_FOREIGN_ELEMENTS = frozenset(("svg", "math"))

#: Authors are joined with this, which is also how Trafilatura joined them.
#: Not a comma: a name can hold one ("Doe, Jane").
AUTHOR_SEPARATOR = "; "

#: Elements with no end tag, which therefore never enclose anything.
_VOID_ELEMENTS = frozenset(
    "area base br col embed hr img input link meta param source track wbr".split()
)

#: Elements whose microdata value is a URL, not text (WHATWG HTML, microdata
#: "values"): an author given this way is an address, not a name.
_URL_VALUED = frozenset(
    "a area audio embed iframe img link object source track video".split()
)

#: Elements a name is not made of.  An author element read for its text that
#: holds one of these holds more than a name, an author's box with a picture
#: and a biography, say, and it is not read.
_BLOCK_ELEMENTS = frozenset(
    (
        "address article aside blockquote dd details dialog div dl dt fieldset "
        "figcaption figure footer form h1 h2 h3 h4 h5 h6 header hgroup hr li "
        "main menu nav ol p pre section summary table tbody td tfoot th thead "
        "tr ul"
    ).split()
)

#: An absolute web address: a value that names where an author or a site is,
#: not who or what it is.
_WEB_ADDRESS = re.compile(r"https?://", re.IGNORECASE)

# A printed byline (docs/09 P124-3).  P88 stopped reading one because
# Trafilatura read whatever an element classed or linked as an author held:
# each listed post's author on an index page, a name run into a job title or
# the labels of social links, a commit's author.  The rules here read text,
# never a class, and each refuses one of those shapes.

#: Elements whose text is never a byline's: code, and what the page does not
#: show as text.
_QUIET_ELEMENTS = frozenset(
    "script style noscript template textarea title svg math select option".split()
)

#: Elements whose text the byline rules read.  Not `h1`, the title.
_BYLINE_ELEMENTS = frozenset(
    "p div span address li dd dt small strong b em i cite a h2 h3 h4 h5 h6".split()
)

#: Elements whose contents are another thing than the article's own text: the
#: site's menus, a sidebar, a picture's caption and credit, a control.
_ASIDE_ELEMENTS = frozenset("nav aside figure figcaption button label".split())

_HEADINGS = {f"h{rank}": rank for rank in range(1, 7)}

#: A byline "By …" counts only this close after the page's first `<h1>`, in
#: characters of text, where an article prints its byline.  A listed post or
#: a related article further down the page has a byline of its own, and on an
#: index page there is no one byline to read.
BYLINE_WINDOW = 1500

#: A line of text longer than this is a sentence, not a byline.
_BYLINE_LINE = 160

#: "By …" or "Written by …", and the names after it.
_BY = re.compile(r"(?:written\s+)?by:?\s+(.+)", re.IGNORECASE)

#: A heading that labels the names after it as the article's authors.
_AUTHOR_LABEL = re.compile(
    r"(?:(?:about\s+)?the\s+)?authors?(?:\s*\(s\))?|written\s+by|about\s+the\s+authors?",
    re.IGNORECASE,
)

#: More names than this under an author heading is a list of people, not a
#: byline: a contributors page, say.
_MAX_BYLINE_NAMES = 8

#: More text than this between an author heading and the end of its section
#: is a section of the article that happens to be headed so, not a byline's
#: names, their contacts and a line of biography.
_AUTHOR_SECTION_TEXT = 600

#: What ends the names in a byline line: a date or a section after a bar or a
#: dash, as "By Jane Doe | March 3, 2026" has it.
_BYLINE_END = re.compile(
    r"\s*[|\N{MIDDLE DOT}\N{BULLET}]\s*|\s+[\N{EM DASH}\N{EN DASH}-]\s+"
)

#: What separates the names in a byline line.
_NAME_SEPARATOR = re.compile(r"\s*[,;&]\s*|\s+and\s+", re.IGNORECASE)

#: A word of a name, for telling whether a byline names someone the
#: declaration does not: its letters, without the full stops, apostrophes and
#: hyphens a name can be spelled with or without.
_WORD = re.compile(r"[^\W\d_]+")

#: Words of a name that are not capitalized: "Ludwig van Beethoven".
_NAME_PARTICLES = frozenset(
    "da das de del della der des di do dos du el la le van von bin ibn al y e".split()
)

#: Words no person's name holds, so that a title-cased phrase after "By" is
#: not one: "By The Numbers", "By Our Readers", "By This Logic".
_NOT_NAME_WORDS = frozenset(
    (
        "the a an this that these those our your my his her their its all "
        "every each some any no how what why when where which who whom whose "
        "of for from with without into onto upon over under about after before"
    ).split()
)

#: Words that make a part of a byline a role, an organization or a section
#: of the site, not a person: "By Jane Doe, Staff Writer" names Jane Doe and
#: her role, a financial news site printed "By" before a link to its "Stock
#: Markets" section, and a business magazine printed its council's name and
#: "Councils Member" after a contributor's (docs/09 P124-3).  Some are
#: surnames too ("Guest", "Press", "Senior"), and a name holding one is passed
#: over with the roles, which is why a byline that names only some of the
#: declared people does not replace them.  An organization's name that holds
#: none of them ("Harbor Lane Review") reads as a name.
_ROLE_WORDS = frozenset(
    (
        "staff writer writers editor editors editorial reporter reporters "
        "correspondent contributor contributors contributing columnist producer "
        "analyst team desk bureau news newsroom press wire agency service "
        "services office department partner associate counsel director "
        "professor fellow senior chief special guest intern freelance "
        "photographer illustrator university school college institute inc llc "
        "llp ltd business market markets stock stocks economy finance politics "
        "technology tech sports opinion world health science culture "
        "entertainment lifestyle travel member members council councils"
    ).split()
)

#: Below this, "the main content" is not a claim worth making: a page with a
#: paragraph on it has no main region to isolate, and matching a handful of
#: words back into the DOM would land on something arbitrary.
MINIMUM_MAIN_CONTENT_CHARACTERS = 200


@dataclass(frozen=True, slots=True)
class PageReading:
    """Everything Trafilatura has to say about one captured page.

    One object, and one call, because both answers come from parsing the same
    HTML: reading them separately would parse it twice and — worse — describe
    two different moments of a live DOM.  It also means the caller decides
    once that this work belongs off the event loop.
    """

    metadata: DiscoveredMetadata
    #: The article's text, or None when the page has no main region worth
    #: isolating.  A probe for the DOM search, never published.
    article: str | None
    #: The titles of each article the page declares in JSON-LD
    #: (`declared_article_titles`), for the check that the capture holds one
    #: of them.  Never published.
    article_titles: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True, slots=True)
class DiscoveredMetadata:
    """What the page says about itself: the title, date, author and site name
    it declares (`declared_title`, `declared_date`, `declared_author`,
    `declared_sitename`).

    Empty strings rather than nulls, matching the `page_metadata` record the
    DOM side already publishes: a consumer reads one shape for both.  Empty
    means the page declares none that WebShot reads.
    """

    #: The title the page declares, as it writes it, the whole of its
    #: `<title>` when it declares none in a `<meta>` or JSON-LD.  Never a
    #: heading's text or a part of the `<title>` (docs/09 P93).
    title: str = ""
    #: The names the page declares, as it writes them, joined with
    #: `AUTHOR_SEPARATOR`; or the names of its printed byline, when it prints
    #: one that names someone they do not (docs/09 P88, P124-3).
    author: str = ""
    #: `YYYY-MM-DD`, or empty when the page declares no date that
    #: `declared_date` reads. Never one found in the text, the URL or a
    #: heading (docs/09 P82), nor one marked inside a link to another page
    #: (P124-4).
    date: str = ""
    #: The site name the page declares, as it writes it.  Never a part of the
    #: `<title>` or the URL's host (docs/09 P88).
    sitename: str = ""
    #: The names the page prints as its byline (`printed_byline`), as it
    #: prints them, joined with `AUTHOR_SEPARATOR`.  Recorded only when there
    #: is one.
    byline: str = ""
    #: What the page's declarations name, when `author` is the byline
    #: instead because it names someone they do not.  Recorded only then.
    declared_author: str = ""

    def record(self) -> dict[str, str]:
        """The manifest's `discovered_metadata`: the four fields always, the
        byline and the declared author only when there is one, so that a page
        without a byline records what it always did."""
        record = {
            "title": self.title,
            "author": self.author,
            "date": self.date,
            "sitename": self.sitename,
        }
        if self.byline:
            record["byline"] = self.byline
        if self.declared_author:
            record["declared_author"] = self.declared_author
        return record


def read_page(html: str) -> PageReading:
    """Both readings of one page, in one blocking call."""
    reader = _read_declarations(html)
    return PageReading(
        metadata=_metadata(reader),
        article=main_content_text(html),
        article_titles=_article_titles(reader),
    )


def read_metadata(html: str) -> DiscoveredMetadata:
    """Read the title, date, author and site name the page declares.

    Only the markup is read.  There is no address to read: a URL's path is
    where htmldate found a date and its host where Trafilatura found a site
    name (docs/09 P82, P88), and neither is a declaration.
    """
    return _metadata(_read_declarations(html))


def declared_article_titles(html: str) -> tuple[tuple[str, ...], ...]:
    """The titles of each article the page declares, in document order.

    An article is a schema.org Article, or a type beneath it
    (`_JSON_LD_ARTICLE_TYPES`), at the top of a JSON-LD block as
    `declared_title` reads one, and its titles are its `headline` and its
    `name`, those it states, as it writes them with each run of white space
    made one space.  Both, because a news site's `headline` is the one it
    prints and an encyclopedia's is a one-line description beside the `name`
    it prints (`_json_ld_titles`).  A WebPage is not an article: its `name`
    is often the site's.
    """
    return _article_titles(_read_declarations(html))


def _article_titles(reader: _Declarations | None) -> tuple[tuple[str, ...], ...]:
    return tuple(reader.article_titles) if reader is not None else ()


def _metadata(reader: _Declarations | None) -> DiscoveredMetadata:
    declared = _author(reader)
    byline = _byline(reader, declared)
    # The byline replaces a declaration when it names someone the
    # declaration does not: what the page prints is what its reader is told,
    # and the declaration was a CMS's record of who posted it (docs/09
    # P124-3).  A byline that names only declared people is no evidence
    # against them, since its reading passes over a name holding a role's
    # word and so can name fewer people than the page prints.  With no
    # declaration, `author` keeps P88's rule and the byline is recorded on
    # its own.
    replaced = bool(
        byline
        and declared
        and not _same_people(byline, declared)
        and _names_someone_else(byline, declared)
    )
    return DiscoveredMetadata(
        title=_title(reader),
        author=byline if replaced else declared,
        date=_date(reader),
        sitename=_sitename(reader),
        byline=byline,
        declared_author=declared if replaced else "",
    )


def declared_title(html: str) -> str:
    """The title the page declares for itself, or "".

    The first of these the page declares, in this order:

    1. `<meta property="og:title">`;
    2. `<meta name="twitter:title">`;
    3. the `name`, or else the `headline`, of a schema.org Article or WebPage
       (`_JSON_LD_TITLED_TYPES`) at the top of a JSON-LD block: the block
       itself, an item of a top-level array, or a node of a top-level
       `@graph`;
    4. `<meta name="citation_title">`;
    5. `<meta name="DC.title">` or `DCTERMS.title`;
    6. the `<title>`: the first one outside an `<svg>` or `<math>`, which is
       the one a browser shows.

    Among values of one kind the first in the document wins, and a `<meta>`
    is read whether it names its key with `property` or `name`.  The value is
    kept as the page writes it, with each run of white space made one space,
    so a `<title>` of "Field Notes | Harbor Lane" is recorded whole: cutting
    the site's name off would mean guessing which side of which separator is
    the title.  A web address is kept too: unlike an author's, a title given
    as an address is still the page's title.  Never the text of a heading,
    which is where Trafilatura's title came from when a page had one `<h1>`.
    """
    return _title(_read_declarations(html))


def declared_date(html: str) -> str:
    """The date the page declares for itself, as `YYYY-MM-DD`, or "".

    Read from four kinds of declaration and nothing else:

    - a `<meta>` whose key `_META_DATE_KEYS` names;
    - schema.org's `datePublished`, `dateCreated` or `dateModified`, in a
      `<script type="application/ld+json">` block (from its outermost objects
      that state one) or as a microdata `itemprop` with a `content` or
      `datetime` value;
    - a `<time>` marked `pubdate`, read as a publication;
    - any other `<time>`, from its `datetime`, or its text when it has none.

    A publication or creation beats a modification, and both beat a plain
    `<time>`; among equals the first in the document wins.  A value that is not
    a date `_DECLARED_DATE` reads is passed over, not guessed at.  A year or
    a month alone (`2019`, `2019-03`) is not a date: recording it as the 1st
    would invent a day, which is what htmldate does with a copyright year.
    """
    return _date(_read_declarations(html))


def declared_author(html: str) -> str:
    """The authors the page declares for itself, joined with
    `AUTHOR_SEPARATOR`, or "".

    Read from six kinds of declaration and nothing else, and from the first
    of them in this order that the page uses:

    1. schema.org's `author` in a `<script type="application/ld+json">`
       block: a name, an object's `name` (a Person's, an Organization's), or
       a list of them, from the block's outermost objects that state one;
    2. microdata's `itemprop="author"`: its `content`, its text, or, when it
       is an item of its own (`itemscope`), that item's `name`; from the
       outermost items that state one;
    3. `<meta name="author">`;
    4. `<meta name="citation_author">`;
    5. `<meta property="article:author">`;
    6. `<meta name="DC.creator">` or `DCTERMS.creator`.

    Every name of that kind is kept, in document order, once each, as the
    page writes it, with each run of white space made one space.  A value
    that is a web address (`https://…`, as `article:author` often is) says
    where an author is described, not who they are, and is passed over.  So
    is a microdata author on a link or a picture, whose value is a URL, and
    one read for its text whose element holds a block: an author's box with
    a biography in it is not a name.  An author stated in a form none of
    these reads (a JSON-LD `{"@id": …}` reference, say) still ends the
    search at its level: the authors of the comments below it are not the
    page's.  Nothing is read from a byline's class or text, a heading,
    `rel="author"` or `twitter:creator`: `printed_byline` reads a byline.
    """
    return _author(_read_declarations(html))


def printed_byline(html: str) -> str:
    """The names the page prints as its byline, joined with
    `AUTHOR_SEPARATOR`, or "" (docs/09 P124-3).

    Read from the page's text in two shapes, and nowhere else:

    1. a line "By Jane Doe" or "Written by Jane Doe and John Roe", not a
       heading, in the `BYLINE_WINDOW` characters after the page's first
       `<h1>`, where an article prints its byline: what follows a bar or a
       spaced dash (a date, a section) is not read, and the names are what is
       between the commas, semicolons, ampersands and "and"s;
    2. a heading whose whole text labels authors ("Author", "Authors",
       "Author(s)", "About the author", "Written by"), and the names in the
       lower headings after it and in the `<strong>` or `<b>` elements that
       open their block, as a biography's subject does, up to the next
       heading of its rank or the end of the section around it, when that
       comes within `_AUTHOR_SECTION_TEXT` characters.  What a biography sets
       in bold inside its sentence, the review its subject edits, say, is not
       read.

    A name is two to five words, each capitalized but for a particle such as
    "van", with no digit and nothing but letters, apostrophes, hyphens and
    full stops, and none of them a word such as "the", "our" or "of" that no
    name holds.  A part holding a word of a role, an organization or a
    section (`_ROLE_WORDS`: "Staff Writer", "Harbor Lane News") is not a name
    and is passed over, and so is a name that holds one ("Rowan Guest"),
    unless the page's declarations name it by its words and a byline prints
    it beside a plain name, either after "and" or "&" or with the
    declarations naming that byline's plain names too: then it is a name in
    every byline that prints it (docs/09 P124-6).  Without that, the byline
    can name fewer people than the page prints; an organization's name that
    holds none ("Harbor Lane Review") reads as a name.  Text inside a
    link, a menu, a sidebar, a figure or its caption, a form or a control is
    never a byline, and nor is code.  More than eight names under one heading
    is a list of people, not a byline.  Every byline the page prints must
    name the same people, in any order and spelling of case and punctuation:
    a page with two that differ prints no one byline, and none is read.
    """
    reader = _read_declarations(html)
    return _byline(reader, _author(reader))


def declared_sitename(html: str) -> str:
    """The site name the page declares for itself, or "".

    The first of these the page declares, in this order: `og:site_name`;
    the `name` of a schema.org `WebSite` in JSON-LD, from the outermost level
    of a block that holds one; the name of a JSON-LD `publisher`, from the
    outermost objects that state one; `<meta name="application-name">`.
    Among values of one kind the first in the document wins.  A web address
    is passed over, as it is for an author.  Never a part of the `<title>`
    or the URL's host, which is where Trafilatura's own site name can come
    from.
    """
    return _sitename(_read_declarations(html))


def _read_declarations(html: str) -> _Declarations | None:
    reader = _Declarations()
    try:
        reader.feed(html)
        reader.close()
    except Exception as exc:  # a capture must never fail over its metadata
        LOGGER.debug("Declarations could not be read: %s", exc)
        return None
    return reader


def _date(reader: _Declarations | None) -> str:
    if reader is None:
        return ""
    best: tuple[int, str] | None = None
    for tier, value in reader.dates:
        stated = _calendar_date(value)
        if stated and (best is None or tier < best[0]):
            best = (tier, stated)
    return best[1] if best else ""


def _author(reader: _Declarations | None) -> str:
    if reader is None:
        return ""
    declarations = list(reader.authors)
    if reader.microdata_authors:
        outermost = min(depth for depth, _, _ in reader.microdata_authors)
        declarations.extend(
            (_MICRODATA_AUTHOR, value)
            for depth, _, value in sorted(
                reader.microdata_authors, key=lambda author: author[1]
            )
            if depth == outermost
        )
    return AUTHOR_SEPARATOR.join(_first_kind(declarations))


def _byline(reader: _Declarations | None, declared: str) -> str:
    if reader is None:
        return ""
    # Who is a person is settled once, from every byline's evidence, and then
    # holds in each: confirmed in one byline and passed over in another, one
    # co-author would make the page's two bylines disagree (docs/09 P124-6).
    people = _confirmed(reader.bylines, declared)
    bylines = [
        names
        for names in (
            tuple(
                name.text
                for name in byline
                if not name.doubtful
                or any(_same_people(name.text, person) for person in people)
            )
            for byline in reader.bylines
        )
        if names
    ]
    if not bylines:
        return ""
    first = bylines[0]
    if any(not _same_people("; ".join(names), "; ".join(first)) for names in bylines):
        return ""
    return AUTHOR_SEPARATOR.join(first)


def _confirmed(bylines: list[tuple[_BylineName, ...]], declared: str) -> list[str]:
    """The names holding a role's word that are people: the page's
    declarations name them by the same words, and a byline that prints them
    beside a plain name either prints them after "and" or "&" or names no one
    else the declarations do not.  A publisher declared as the author and
    printed after a comma, "By Jane Doe, Harbor Lane News", is the writer's
    organization, not a second writer; and a byline of nothing but such
    names, "By Harbor Lane News", names no one for certain, since
    "declared" is then all that is known of them (docs/09 P124-6)."""
    values = _declared_values(declared)
    people: list[str] = []
    for byline in bylines:
        plain = [name for name in byline if not name.doubtful]
        if not plain:
            continue
        every_one = all(_is_declared(name.text, values) for name in plain)
        people.extend(
            name.text
            for name in byline
            if name.doubtful
            and _is_declared(name.text, values)
            and (name.joined or every_one)
        )
    return people


def _same_people(one: str, other: str) -> bool:
    """Whether two lists of names name the same people: the same letters,
    whatever the order, the case, the punctuation and the separators, so
    "R. Chen" is "R Chen" and "Doe, Jane" is "Jane Doe"."""
    return _letters(one) == _letters(other)


def _letters(names: str) -> list[str]:
    return sorted(character for character in names.casefold() if character.isalpha())


def _names_someone_else(byline: str, declared: str) -> bool:
    """Whether a byline names someone the declaration does not: a name of it
    whose words, whatever their case and punctuation, are not all among the
    words of one declared value.  One value can hold several names
    ("Jane Doe and John Roe") or one written surname first ("Doe, Jane"),
    which is why a name is looked for among a value's words."""
    values = _declared_values(declared)
    return any(
        not _is_declared(name, values) for name in byline.split(AUTHOR_SEPARATOR)
    )


def _declared_values(declared: str) -> list[set[str]]:
    """The words of each declared value."""
    return [set(_words(value)) for value in declared.split(AUTHOR_SEPARATOR)]


def _is_declared(name: str, values: list[set[str]]) -> bool:
    """Whether a name's words, whatever their case and punctuation, are all
    among the words of one declared value."""
    words = set(_words(name))
    return bool(words) and any(words <= value for value in values)


def _words(names: str) -> list[str]:
    return _WORD.findall(names.casefold())


def _byline_names(text: str) -> list[_BylineName]:
    """The names in what follows "By", or none when any part of it is neither
    a name nor holds a role's word.  A part that holds one is passed over as
    a role, unless it reads as a name, "Rowan Guest" say (`_ROLE_WORDS`):
    that is kept as doubtful, for the declarations to confirm or not
    (`_confirmed`), with whether "and" or "&" came before it."""
    line = _BYLINE_END.split(text, maxsplit=1)[0]
    names: list[_BylineName] = []
    start, before = 0, ""
    for separator in [*_NAME_SEPARATOR.finditer(line), None]:
        part = line[start : separator.start() if separator else len(line)]
        if separator is not None:
            start, after = separator.end(), separator.group()
        if part and all(name.text != part for name in names):
            joined = before.strip().casefold() in ("and", "&")
            if _is_role(part):
                if _name_shaped(part):
                    names.append(_BylineName(part, doubtful=True, joined=joined))
            elif _is_name(part):
                names.append(_BylineName(part, joined=joined))
            else:
                return []
        if separator is not None:
            before = after
    return names


def _is_role(part: str) -> bool:
    return any(word.strip(".").casefold() in _ROLE_WORDS for word in part.split())


def _is_name(part: str) -> bool:
    return _name_shaped(part) and not _is_role(part)


def _name_shaped(part: str) -> bool:
    """Two to five capitalized words a name could hold, whether or not one
    of them is a role's."""
    words = part.split()
    if not 2 <= len(words) <= 5 or len(part) > 60:
        return False
    for word in words:
        if word.casefold() in _NAME_PARTICLES:
            continue
        if word.strip(".").casefold() in _NOT_NAME_WORDS:
            return False
        if not word[0].isupper() or not all(
            character.isalpha() or character in "'\N{RIGHT SINGLE QUOTATION MARK}-."
            for character in word
        ):
            return False
    return True


def _sitename(reader: _Declarations | None) -> str:
    if reader is None:
        return ""
    names = _first_kind(reader.sitenames)
    return names[0] if names else ""


def _title(reader: _Declarations | None) -> str:
    if reader is None:
        return ""
    titles = _first_kind(reader.titles, addresses=True)
    return titles[0] if titles else ""


def _first_kind(
    declarations: list[tuple[int, str]], *, addresses: bool = False
) -> list[str]:
    """The readable values of the most preferred kind present, in order and
    once each.  A kind whose every value is unreadable is not present.

    A web address is unreadable unless `addresses` says it is a value: it
    says where a person or a site is described, not what they are called.
    """
    kinds: dict[int, list[str]] = {}
    for kind, value in declarations:
        value = " ".join(value.split())
        if value and (addresses or not _WEB_ADDRESS.match(value)):
            values = kinds.setdefault(kind, [])
            if value not in values:
                values.append(value)
    return kinds[min(kinds)] if kinds else []


def _calendar_date(value: str) -> str:
    match = _DECLARED_DATE.match(value.strip())
    if match is None:
        return ""
    year, _, month, day = match.groups()
    try:
        return datetime.date(int(year), int(month), int(day)).isoformat()
    except ValueError:  # 2024-02-30, or the 0000-00-00 some CMSs write for none
        return ""


@dataclass(eq=False, slots=True)
class _Text:
    """The text of an open element whose text is a property's value."""

    parts: list[str] = field(default_factory=list)
    #: The element turned out to hold a block, so its text is not a name.
    spoiled: bool = False


@dataclass(eq=False, slots=True)
class _AuthorItem:
    """A microdata author that is an item, whose `name` is the author."""

    depth: int
    order: int
    name: str | None = None


@dataclass(eq=False, slots=True)
class _Element:
    """An open element, as much of it as reading microdata needs."""

    tag: str
    #: The element is an item (`itemscope`): what it encloses is its own.
    scope: bool
    #: The element is an author read from its text: its depth and order.
    author: tuple[int, int] | None = None
    #: The element is an author that is an item.
    item: _AuthorItem | None = None
    #: The element is the `name` of that author item.
    name_of: _AuthorItem | None = None
    text: _Text | None = None
    #: A link's `href`, or None for an element that is not a link, an `<a>`
    #: with no `href` among them.
    href: str | None = None
    #: The element's text, read for a byline.
    line: _Line | None = None
    #: Characters of text the page had shown when the element opened.
    shown: int = 0
    #: A `<strong>` or `<b>` under an author heading that opened its block,
    #: as a biography's subject does (`_opens_its_block`).
    leads: bool = False


@dataclass(eq=False, slots=True)
class _Line:
    """An element's text, while it is short enough to be a byline."""

    parts: list[str] = field(default_factory=list)
    length: int = 0
    #: It grew past `_BYLINE_LINE`: a sentence, not a byline.
    overflowed: bool = False
    #: It is inside a link, a menu, a sidebar, a figure or a control.
    aside: bool = False
    #: It opened within `BYLINE_WINDOW` of the first `<h1>`.
    near_title: bool = False


@dataclass(frozen=True, slots=True)
class _BylineName:
    """A name a byline prints."""

    text: str
    #: It reads as a name but holds a role's word, so that only the page's
    #: declarations can tell a writer ("Rowan Guest") from a role or an
    #: organization ("Guest Contributor", "Harbor Lane News") (docs/09
    #: P124-6).
    doubtful: bool = False
    #: It follows "and" or "&", which join a writer to a writer; a comma also
    #: sets a role or an organization after a name.
    joined: bool = False


@dataclass(eq=False, slots=True)
class _Label:
    """An author heading, and the names after it so far."""

    rank: int
    #: The section, article or aside around the heading, whose end ends it.
    within: _Element | None
    names: list[_BylineName] = field(default_factory=list)
    #: Characters of text since the heading.
    text: int = 0
    #: More names than `_MAX_BYLINE_NAMES`, or more text than
    #: `_AUTHOR_SECTION_TEXT`.
    crowded: bool = False


class _Declarations(HTMLParser):
    """Every title, date, author and site name declaration in a page, in
    document order, each with its tier or kind.

    The standard library's parser rather than Trafilatura's tree, so that what
    counts as a declaration is this module's rule and no upstream's: the point
    is that an htmldate or Trafilatura release cannot change it.

    Microdata belongs to the item around it, so the open elements are kept on
    a stack, and an end tag closes the innermost open element it names and
    whatever it encloses.  That is exact on what WebShot reads, Chromium's
    serialization of the DOM, which writes the end tag of every element that
    is not void.  Hand-written HTML that leaves a `</p>` or a `</li>` out
    leaves that element open until an end tag around it, so an author read
    for its text from such an element runs on into what follows; once that
    takes in a block (`_BLOCK_ELEMENTS`) it is no longer a name, and it is not
    read.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.dates: list[tuple[int, str]] = []
        #: The authors of every kind but microdata, as (kind, value).
        self.authors: list[tuple[int, str]] = []
        #: Microdata's authors, as (item depth, place in the document,
        #: value), the value "" for one stated in a form that is not a name.
        self.microdata_authors: list[tuple[int, int, str]] = []
        self.sitenames: list[tuple[int, str]] = []
        self.titles: list[tuple[int, str]] = []
        #: Each declared article's titles (`declared_article_titles`).
        self.article_titles: list[tuple[str, ...]] = []
        #: The open `<time>` whose text is its value, and that text so far.
        self._time: tuple[int, list[str]] | None = None
        #: The document's `<title>` while it is open, and its text so far.
        self._title: list[str] | None = None
        #: The document's `<title>` has opened: only the first is the page's.
        self._titled = False
        #: The open JSON-LD block's text so far.
        self._json_ld: list[str] | None = None
        self._open: list[_Element] = []
        #: The texts being read, innermost last.
        self._texts: list[_Text] = []
        #: How many microdata authors have opened, which orders them.
        self._places = 0
        #: The page's own address, as its canonical link and `og:url` state
        #: it: a `<time>` in a link to it is the page's date (docs/09 P124-4).
        self.own_addresses: list[str] = []
        #: Each byline the page prints, as its names (`printed_byline`).
        self.bylines: list[tuple[_BylineName, ...]] = []
        #: The elements whose text is being read for a byline, innermost last.
        self._lines: list[_Line] = []
        #: Open elements whose text is code or is not shown, and open asides.
        self._quiet = 0
        self._asides = 0
        #: Characters of text since the first `<h1>` opened, or None before.
        self._since_title: int | None = None
        #: Characters of text the page has shown, outside code and what is
        #: not shown.
        self._shown = 0
        #: The author heading whose names are being read.
        self._label: _Label | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name: value or "" for name, value in attrs}
        self._date_declarations(tag, attributes)
        if tag == "link" and "canonical" in attributes.get("rel", "").lower().split():
            if attributes.get("href"):
                self.own_addresses.append(attributes["href"])
        rank = _HEADINGS.get(tag)
        if rank is not None and self._label is not None and rank <= self._label.rank:
            self._end_label()
        if (
            tag == "h1"
            and self._since_title is None
            and not self._quiet
            and not self._asides
        ):
            self._since_title = 0
        if tag == "meta" and attributes.get("content"):
            keys = dict.fromkeys(
                _meta_key(attributes.get(name)) for name in ("property", "name")
            )
            for key in keys:
                if key == "og:url":
                    self.own_addresses.append(attributes["content"])
                if key in _META_AUTHOR_KEYS:
                    self.authors.append((_META_AUTHOR_KEYS[key], attributes["content"]))
                if key in _META_SITENAME_KEYS:
                    self.sitenames.append(
                        (_META_SITENAME_KEYS[key], attributes["content"])
                    )
                if key in _META_TITLE_KEYS:
                    self.titles.append((_META_TITLE_KEYS[key], attributes["content"]))
        elif tag == "title" and not self._titled and not self._in_foreign_content():
            # The first, even when it is empty: it is the one a browser shows.
            self._titled, self._title = True, []
        self._microdata(tag, attributes)

    def _in_foreign_content(self) -> bool:
        """An element opening now is SVG's or MathML's, not HTML's."""
        return any(element.tag in _FOREIGN_ELEMENTS for element in self._open)

    def _date_declarations(self, tag: str, attributes: dict[str, str]) -> None:
        itemprop = _ITEMPROP_DATES.get(attributes.get("itemprop", "").lower())
        if tag == "meta":
            keys = (
                _meta_key(attributes.get(name))
                for name in ("property", "name", "itemprop", "http-equiv")
            )
            tiers = [_META_DATE_KEYS[key] for key in keys if key in _META_DATE_KEYS]
            if tiers and "content" in attributes:
                self.dates.append((min(tiers), attributes["content"]))
        elif tag == "time":
            # A date marked inside a link is the date of what the link opens:
            # a related article's card, a listed post.  It is the page's own
            # only when the link opens the page itself, as a post's permalink
            # does (docs/09 P124-4).
            link = self._innermost_link()
            if link is not None and not self._opens_this_page(link):
                return
            tier = _MARKED
            if itemprop is not None:
                tier = itemprop
            elif "pubdate" in attributes:
                tier = _ORIGINAL
            if "datetime" in attributes:
                self.dates.append((tier, attributes["datetime"]))
            elif self._time is None:
                self._time = (tier, [])
        elif itemprop is not None:
            value = attributes.get("content", attributes.get("datetime"))
            if value is not None:
                self.dates.append((itemprop, value))
        elif tag == "script" and _is_json_ld(attributes.get("type", "")):
            self._json_ld = []

    def _microdata(self, tag: str, attributes: dict[str, str]) -> None:
        if tag in _BLOCK_ELEMENTS:
            for text in self._texts:
                text.spoiled = True
        void = tag in _VOID_ELEMENTS
        element = _Element(
            tag, scope="itemscope" in attributes and not void, shown=self._shown
        )
        if tag == "a" and "href" in attributes:
            element.href = attributes["href"]
        if not void:
            self._open_line(element)
        properties = attributes.get("itemprop", "").lower().split()
        if "name" in properties:
            # Before this element opens: a name is its enclosing item's.
            item = self._innermost_item()
            if item is not None and item.name is None:
                value = _attribute_value(tag, attributes)
                if value is None:
                    element.name_of, element.text = item, _Text()
                elif value.strip():
                    item.name = value
        if "author" in properties:
            depth = sum(1 for open_element in self._open if open_element.scope)
            place = self._places = self._places + 1
            if element.scope:
                element.item = _AuthorItem(depth, place)
            else:
                value = _attribute_value(tag, attributes)
                if value is None:
                    element.author = (depth, place)
                    element.text = element.text or _Text()
                else:
                    self.microdata_authors.append((depth, place, value))
        if not void:
            if element.text is not None:
                self._texts.append(element.text)
            self._open.append(element)

    def _open_line(self, element: _Element) -> None:
        """Count what the element opens, and read its text for a byline when
        it is an element whose text can be one."""
        if element.tag in _QUIET_ELEMENTS:
            self._quiet += 1
        if self._quiet:
            return
        # An element's edge is a word's edge in a byline: "By<a>Jane Doe</a>"
        # is set apart by the page's style, and reads "By Jane Doe".
        for line in self._lines:
            line.parts.append(" ")
        aside = (
            bool(self._asides)
            or element.href is not None
            or self._innermost_link() is not None
        )
        if element.tag in _ASIDE_ELEMENTS:
            self._asides += 1
        if element.tag in ("strong", "b") and self._label is not None:
            element.leads = self._opens_its_block(self._label)
        if element.tag in _BYLINE_ELEMENTS:
            element.line = _Line(
                aside=aside,
                near_title=self._since_title is not None
                and self._since_title <= BYLINE_WINDOW,
            )
            self._lines.append(element.line)

    def _end_line(self, element: _Element) -> None:
        """Uncount what the element opened, and read its text for a byline."""
        if element.tag in _QUIET_ELEMENTS:
            self._quiet -= 1
        elif element.tag in _ASIDE_ELEMENTS and not self._quiet:
            self._asides -= 1
        if self._label is not None and element is self._label.within:
            self._end_label()
        line = element.line
        if line is None:
            return
        if not line.overflowed:
            self._lines.remove(line)
        if line.overflowed or line.aside:
            return
        text = " ".join("".join(line.parts).split())
        rank = _HEADINGS.get(element.tag)
        if self._label is not None and (
            (rank is not None and rank > self._label.rank) or element.leads
        ):
            shaped = _name_shaped(text)
            if shaped and all(name.text != text for name in self._label.names):
                self._label.names.append(_BylineName(text, doubtful=_is_role(text)))
                # A role set as a subhead under each writer ("Staff Writer")
                # is no more people.
                sure = sum(not name.doubtful for name in self._label.names)
                if sure > _MAX_BYLINE_NAMES:
                    self._label.crowded = True
        if rank is not None and _AUTHOR_LABEL.fullmatch(text.rstrip(":").strip()):
            self._end_label()
            within = next(
                (
                    open_element
                    for open_element in reversed(self._open)
                    if open_element.tag in ("section", "article", "aside", "main")
                ),
                None,
            )
            self._label = _Label(rank, within)
        # Not a heading's: a subhead such as "By Industry Sector" is set as
        # one, and a byline is not.
        if line.near_title and rank is None:
            match = _BY.fullmatch(text)
            names = _byline_names(match.group(1)) if match else []
            if names:
                self.bylines.append(tuple(names))

    def _opens_its_block(self, label: _Label) -> bool:
        """Whether an element opening now is the first text of its block: no
        text has shown since the innermost open block opened, or since the
        author heading when that came later, as in a section that holds the
        heading and the name.  A biography sets its subject first; what it
        sets in bold inside a sentence, the review its subject edits, say, is
        not its subject (docs/09 P124-3)."""
        block = next(
            (
                open_element
                for open_element in reversed(self._open)
                if open_element.tag in _BLOCK_ELEMENTS
            ),
            None,
        )
        since_block = self._shown - (block.shown if block is not None else 0)
        return min(since_block, label.text) == 0

    def _end_label(self) -> None:
        label, self._label = self._label, None
        if label is not None and label.names and not label.crowded:
            self.bylines.append(tuple(label.names))

    def _innermost_link(self) -> _Element | None:
        for element in reversed(self._open):
            if element.href is not None:
                return element
        return None

    def _opens_this_page(self, link: _Element) -> bool:
        """Whether a link opens the page itself, as its canonical address or
        `og:url` names it.  A link to a part of it (`#comment-4`) opens that
        part, a comment's permalink, and not the page."""
        if not link.href or not self.own_addresses:
            return False
        opened = urlsplit(urljoin(self.own_addresses[0], link.href.strip()))
        if opened.fragment:
            return False
        return _address(opened) in {
            _address(urlsplit(address)) for address in self.own_addresses
        }

    def _innermost_item(self) -> _AuthorItem | None:
        """The author item whose properties an element opening now states."""
        for element in reversed(self._open):
            if element.scope:
                return element.item
        return None

    def handle_data(self, data: str) -> None:
        if not self._quiet:
            seen = len(data.strip())
            self._shown += seen
            if self._since_title is not None:
                self._since_title += seen
            if self._label is not None:
                self._label.text += seen
                if self._label.text > _AUTHOR_SECTION_TEXT:
                    self._label.crowded = True
            for line in self._lines:
                line.parts.append(data)
                line.length += len(data)
                line.overflowed = line.length > _BYLINE_LINE
            if any(line.overflowed for line in self._lines):
                self._lines = [line for line in self._lines if not line.overflowed]
        if self._time is not None:
            self._time[1].append(data)
        if self._title is not None:
            self._title.append(data)
        if self._json_ld is not None:
            self._json_ld.append(data)
        for text in self._texts:
            text.parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "time":
            self._end_time()
        elif tag == "title":
            self._end_title()
        elif tag == "script" and self._json_ld is not None:
            self._end_json_ld("".join(self._json_ld))
            self._json_ld = None
        if tag in _VOID_ELEMENTS:  # never opened: `<br/>` arrives as both
            return
        for index in range(len(self._open) - 1, -1, -1):
            if self._open[index].tag == tag:
                while len(self._open) > index:
                    self._end_element(self._open.pop())
                break

    def close(self) -> None:
        super().close()
        self._end_time()  # a <time> the page never closed still said a date
        self._end_title()  # and a <title> its title
        while self._open:  # nor did an author the page never closed
            self._end_element(self._open.pop())
        self._end_label()

    def _end_time(self) -> None:
        if self._time is not None:
            tier, parts = self._time
            self.dates.append((tier, "".join(parts)))
            self._time = None

    def _end_title(self) -> None:
        if self._title is not None:
            self.titles.append((_TITLE_ELEMENT, "".join(self._title)))
            self._title = None

    def _end_json_ld(self, text: str) -> None:
        data = _json_ld(text)
        self.dates.extend(_json_ld_dates(data))
        self.authors.extend(
            (_JSON_LD_AUTHOR, name) for name in _outermost(data, _json_ld_author)
        )
        self.sitenames.extend(
            (_JSON_LD_WEBSITE, name) for name in _outermost(data, _json_ld_website)
        )
        self.sitenames.extend(
            (_JSON_LD_PUBLISHER, name) for name in _outermost(data, _json_ld_publisher)
        )
        self.titles.extend((_JSON_LD_TITLE, title) for title in _json_ld_titles(data))
        self.article_titles.extend(_json_ld_article_titles(data))

    def _end_element(self, element: _Element) -> None:
        self._end_line(element)
        value = None
        if element.text is not None:
            self._texts.remove(element.text)
            if not element.text.spoiled:
                value = "".join(element.text.parts)
        if element.author is not None:
            depth, place = element.author
            self.microdata_authors.append((depth, place, value or ""))
        if (
            element.name_of is not None
            and element.name_of.name is None
            and value is not None
            and value.strip()
        ):
            element.name_of.name = value
        if element.item is not None:
            item = element.item
            self.microdata_authors.append((item.depth, item.order, item.name or ""))


def _address(split: SplitResult) -> tuple[str, str, str]:
    """An address as `_opens_this_page` compares it: the host, the path
    without a trailing slash, and the query, whatever the scheme."""
    return (split.netloc.lower(), split.path.rstrip("/") or "/", split.query)


def _attribute_value(tag: str, attributes: dict[str, str]) -> str | None:
    """A microdata property's value when an attribute holds it, "" when it is
    a URL or there is none, and None when it is the element's text.

    A `content` attribute is read on any element, as it is for the dates.
    """
    if "content" in attributes:
        return attributes["content"]
    if tag == "data":
        return attributes.get("value", "")
    if tag in _URL_VALUED or tag in _VOID_ELEMENTS:
        return ""
    return None


def _meta_key(key: str | None) -> str:
    key = (key or "").strip().lower()
    for prefix in ("dc:", "dcterms:"):
        if key.startswith(prefix):
            return prefix[:-1] + "." + key[len(prefix) :]
    return key


def _is_json_ld(script_type: str) -> bool:
    return script_type.split(";")[0].strip().lower() == "application/ld+json"


def _json_ld(text: str) -> list[object]:
    """One JSON-LD block, as a list of the one value it holds, or empty when
    it is not JSON.  A block that is not JSON is not read: guessing at its
    structure is the kind of reading this module exists to refuse."""
    try:
        return [json.loads(text, strict=False)]
    except (ValueError, RecursionError):
        return []


def _json_ld_dates(data: list[object]) -> list[tuple[int, str]]:
    """The schema.org dates of the outermost objects in one JSON-LD block that
    state any.

    Read level by level, and no deeper than the first level with a date, so
    the dates of a comment or a review nested in an article never count when
    the article states one of its own, whichever kind each is and wherever
    the page put the keys.  The items of an array, `@graph`'s among them, sit
    at the level of the array.
    """
    level = _objects(data)
    while level:
        found: list[tuple[int, str]] = []
        deeper: list[object] = []
        for node in level:
            for key, value in node.items():
                tier = _SCHEMA_ORG_DATES.get(key)
                if tier is not None and isinstance(value, str):
                    found.append((tier, value))
                elif isinstance(value, (dict, list)):
                    deeper.append(value)
        if found:
            return found
        level = _objects(deeper)
    return []


def _outermost(
    data: list[object], state: Callable[[dict[str, object]], list[str] | None]
) -> list[str]:
    """What the outermost objects of one JSON-LD block that state something
    say, level by level as `_json_ld_dates` reads dates.

    `state` gives what one object says, or None when it says nothing of the
    kind.  An object that states something in a form it cannot read gives an
    empty list, and that still ends the search at its level.
    """
    level = _objects(data)
    while level:
        stated = False
        found: list[str] = []
        deeper: list[object] = []
        for node in level:
            said = state(node)
            if said is not None:
                stated = True
                found.extend(said)
            deeper.extend(
                value for value in node.values() if isinstance(value, (dict, list))
            )
        if stated:
            return found
        level = _objects(deeper)
    return []


def _json_ld_author(node: dict[str, object]) -> list[str] | None:
    return _json_ld_names(node["author"]) if node.get("author") else None


def _json_ld_publisher(node: dict[str, object]) -> list[str] | None:
    return _json_ld_names(node["publisher"]) if node.get("publisher") else None


def _json_ld_website(node: dict[str, object]) -> list[str] | None:
    if not _is_a(node, frozenset(("WebSite",))):
        return None
    name = node.get("name")
    return [name] if isinstance(name, str) else []


def _json_ld_titles(data: list[object]) -> list[str]:
    """The titles the top of one JSON-LD block declares, in order.

    Only the top: the block's own object, the items of an array it holds, and
    the nodes of a top-level `@graph`.  An Article one level down is seldom
    the page's own: it is a Blog's post, an ItemList's entry, or a part of a
    collection, and its headline is another page's title.
    """
    titles: list[str] = []
    for node in _objects(data):
        graph = node.get("@graph")
        nodes = _objects([graph]) if isinstance(graph, (list, dict)) else []
        for top in [node, *nodes]:
            if not _is_a(top, _JSON_LD_TITLED_TYPES):
                continue
            # `name` first: an encyclopedia's Article gives its title as the
            # `name` and a one-line description as the `headline`.
            for key in ("name", "headline"):
                value = top.get(key)
                if isinstance(value, str) and value.strip():
                    titles.append(value)
                    break
    return titles


def _json_ld_article_titles(data: list[object]) -> list[tuple[str, ...]]:
    """The `headline` and `name` of each article at the top of one JSON-LD
    block, as `_json_ld_titles` finds its top."""
    found: list[tuple[str, ...]] = []
    for node in _objects(data):
        graph = node.get("@graph")
        nodes = _objects([graph]) if isinstance(graph, (list, dict)) else []
        for top in [node, *nodes]:
            if not _is_a(top, _JSON_LD_ARTICLE_TYPES):
                continue
            titles: list[str] = []
            for key in ("headline", "name"):
                value = top.get(key)
                if isinstance(value, str) and value.strip():
                    title = " ".join(value.split())
                    if title not in titles:
                        titles.append(title)
            if titles:
                found.append(tuple(titles))
    return found


def _is_a(node: dict[str, object], types: frozenset[str]) -> bool:
    """Whether a JSON-LD object's `@type`, or one of them, is in `types`."""
    stated = node.get("@type")
    return any(
        isinstance(name, str) and _schema_org_term(name) in types
        for name in (stated if isinstance(stated, list) else [stated])
    )


def _schema_org_term(name: str) -> str:
    """`WebSite` for `WebSite`, `schema:WebSite` or `https://schema.org/WebSite`."""
    return name.rsplit("/", 1)[-1].rsplit(":", 1)[-1]


def _json_ld_names(value: object) -> list[str]:
    """The names a schema.org value gives: a string, an object's `name`, or a
    list of either.  An object without a string `name` gives none."""
    names: list[str] = []
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict):
            name = item.get("name")
            if isinstance(name, str):
                names.append(name)
        elif isinstance(item, list):
            pending.extend(reversed(item))
    return names


def _objects(values: list[object]) -> list[dict[str, object]]:
    """The objects among `values`, with every array opened in place."""
    objects: list[dict[str, object]] = []
    pending = list(reversed(values))
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            objects.append(value)
        elif isinstance(value, list):
            pending.extend(reversed(value))
    return objects


def main_content_text(html: str) -> str | None:
    """The article text Trafilatura finds, or None if it finds no article.

    `favor_recall` is deliberate: this text is a *probe* used to find an
    element in the DOM, so missing a paragraph costs more than including a
    marginal one.  Tables are included for the same reason — a report whose
    substance is tabular must not look like an empty region.
    """
    try:
        text = extract(
            html,
            favor_recall=True,
            include_tables=True,
            include_comments=False,
            include_formatting=False,
        )
    except Exception as exc:  # the static selector fallback still applies
        LOGGER.debug("Main-content extraction failed: %s", exc)
        return None
    if not text or len(text) < MINIMUM_MAIN_CONTENT_CHARACTERS:
        return None
    return str(text)
