"""A link's printed address does not keep its text off the full stop after it (docs/09 P70-16).

P70-11 joins a formatted element's text to its neighbour only where the
page draws the two touching, and generated content between them counted as
a gap of any size. A print stylesheet that writes each link's address after
it, `a[href]::after { content: " (" attr(href) ")" }` as Wikipedia's and
HTML5 Boilerplate's do, therefore kept every cited link apart from its
punctuation: "44708120 ." in content.txt where master had read "44708120.".
No text surface carries the printed address, and content.md carries it as
the link's target, so on every surface the two are adjacent. The snapshot
now leaves out generated content that is the element's own `href` or `title`
in brackets, and judges the two by the boxes between them. Every other
generated content, and every box, still keeps them apart.

`tests/fixtures/printed_addresses.html` uses made-up words, so each can be
searched for whole. The tests capture it end to end: the evidence is
measured by the snapshot, in the live page.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from pypdf import PdfReader

from webshot.capture.snapshot import TOUCH_ATTRIBUTE

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "printed_addresses.html"

#: Each element's text and what follows it, one run on every text surface:
#: a link and its comma, a link and its full stop with a parenthesis in the
#: address, a protocol-relative address printed with its scheme, a link inside
#: a word, and an abbreviation printed with its title.
JOINED = ("Velkor,", "Dravis.", "Tolmen;", "grevashul", "PO.")
#: What the PDF prints for each: the annotation between.
PRINTED = (
    "Velkor (https://example.invalid/velkor),",
    "Dravis (https://example.invalid/dravis?page=(2)).",
    "Tolmen (https://example.invalid/tolmen);",
    "grevash (https://example.invalid/vash)ul",
    "PO (Plinth Office).",
)
#: Pairs that generated content or a box keeps apart: a separator written as
#: a string, an icon's empty box, and a link's own padding beside its address.
DRAWN_APART = (("Halvor", ","), ("see", "Quessen"), ("Ormask", "."))

_LINK = '<a href="{}" {} rel="noopener noreferrer">'
#: What the snapshot records on each element whose text the markup runs into
#: the text beside it.
RECORDED = (
    _LINK.format("https://example.invalid/velkor", f'{TOUCH_ATTRIBUTE}="after"')
    + "Velkor</a>,",
    _LINK.format(
        "https://example.invalid/dravis?page=(2)", f'{TOUCH_ATTRIBUTE}="after"'
    )
    + "Dravis</a>.",
    _LINK.format("//example.invalid/tolmen", f'{TOUCH_ATTRIBUTE}="after"')
    + "Tolmen</a>;",
    "gre"
    + _LINK.format("https://example.invalid/vash", f'{TOUCH_ATTRIBUTE}="before after"')
    + "vash</a>ul",
    f'<abbr title="Plinth Office" {TOUCH_ATTRIBUTE}="after">PO</abbr>.',
    _LINK.format("#later", f'{TOUCH_ATTRIBUTE}=""') + "Halvor</a>,",
    "see" + _LINK.format("#later", f'{TOUCH_ATTRIBUTE}=""') + "Quessen</a>",
    _LINK.format("https://example.invalid/ormask", f'{TOUCH_ATTRIBUTE}=""')
    + "Ormask</a>.",
)


def _plain(markdown: str) -> str:
    """Markdown with its links, emphasis and code spans taken out. A link's
    target may hold one pair of parentheses, as Dravis's does."""
    links = r"\[([^\]]*)\]\((?:[^()]|\([^()]*\))*\)"
    return re.sub(r"[*`~]", "", re.sub(links, r"\1", markdown))


@pytest.fixture(scope="module")
def captured(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    directory = tmp_path_factory.mktemp("printed-addresses")
    pdf = directory / "printed_addresses.pdf"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "webshot",
            str(FIXTURE),
            "--output",
            str(pdf),
            "--no-ocr",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    bundle = pdf.with_suffix(".ai")
    chunks = [
        json.loads(line)
        for line in (bundle / "chunks.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    return {
        "content.md": (bundle / "content.md").read_text(encoding="utf-8"),
        "content.txt": (bundle / "content.txt").read_text(encoding="utf-8"),
        "chunks.jsonl": "\n".join(chunk["text"] for chunk in chunks),
        "content.html": (bundle / "content.html").read_text(encoding="utf-8"),
        "pdf": "\n".join(
            page.extract_text() or "" for page in PdfReader(str(pdf)).pages
        ),
        "warnings": json.dumps(
            json.loads((bundle / "manifest.json").read_text(encoding="utf-8")).get(
                "warnings"
            )
        ),
    }


@pytest.mark.browser
def test_the_snapshot_leaves_out_an_elements_own_printed_annotation(
    captured: dict[str, str],
) -> None:
    """Each cited link and the abbreviation are recorded touching what follows
    them; the separator, the icon and the padded link are not."""
    html = captured["content.html"]
    for recorded in RECORDED:
        assert recorded in html, recorded
    assert html.count(TOUCH_ATTRIBUTE) == len(RECORDED)


@pytest.mark.browser
def test_every_text_surface_joins_what_only_an_annotation_kept_apart(
    captured: dict[str, str],
) -> None:
    for name in ("content.md", "content.txt", "chunks.jsonl"):
        surface = _plain(captured[name])
        words = set(surface.split())
        for word in JOINED:
            assert word in words, (name, word)
        for left, right in DRAWN_APART:
            assert f"{left}{right}" not in surface, (name, left, right)
            assert re.search(rf"{re.escape(left)}\s+{re.escape(right)}", surface), (
                name,
                left,
                right,
            )
    assert captured["warnings"] == "[]"


@pytest.mark.browser
def test_the_pdf_prints_the_annotation_between(captured: dict[str, str]) -> None:
    """The case is the one it means to be: the PDF prints each address and
    the title where the bundle joins, and the separator, the icon's gap and
    the padding where it does not. Read with whitespace folded, since an
    address wraps."""
    pdf = " ".join(captured["pdf"].split())
    for printed in PRINTED:
        assert re.sub(r"\s+", "", printed) in re.sub(r"\s+", "", pdf), printed
        assert printed.split(" (")[0] in pdf, printed
    for left, right in DRAWN_APART:
        assert f"{left}{right}" not in pdf, (left, right)
    assert "Halvor \N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK}," in pdf
