"""The bundle reads the page a reader sees: shadow roots and frames (docs/09 P16-6).

An open shadow root's content and a same-origin frame's document both print,
so the PDF's text layer had them while `content.html` and every surface built
from it did not, and the manifest said nothing. The one fixture here holds each
kind of text in one sentence. The PDF's text layer is the control: a sentence
the bundle must carry is one the PDF prints, a sentence it must not is one the
PDF does not, and a sentence it cannot read (a closed shadow root, an opaque
frame) is printed and named in a warning.

The passwords matter as much as the text. Playwright's aria snapshot always
read shadow roots and frames, and the redaction beside it asked only the top
document, so a password typed into either reached `accessibility.yaml`.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, cast

import pytest
from playwright.async_api import async_playwright
from playwright.sync_api import sync_playwright
from pypdf import PdfReader

from webshot.bundle.build import _chromium_version
from webshot.capture.session import devtools
from webshot.capture.snapshot import (
    PASSWORD_REDACTION,
    _accessibility_snapshot,
    _closed_hosts,
    _closed_shadow_hosts,
    _holds_text,
    _redact_password_values,
    unread_text_warnings,
    with_frame_content,
)
from webshot.capture.visuals import VisualAsset
from webshot.cli import main
from webshot.ocr.engine import OCRResult
from webshot.ocr.tesseract import tesseract_executable

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "shadow_frames_page.html"
SPOOF_FIXTURE = FIXTURE.with_name("shadow_frame_spoof_page.html")

#: Typed by the fixture's own scripts, after load. They exist to be leaked.
SHADOW_PASSWORD = "Shadow-Pass-3!k"
FRAME_PASSWORD = "Frame-Pass-7!q"

#: Printed, and readable by a page script: every text surface must carry these,
#: in this order.
READABLE = {
    "light text before the host": "Before the host: light text.",
    "a display-contents box": "Contents: text in a display-contents box.",
    "a shadow heading": "Shadowed heading",
    "a node in a named slot": "Slotted title: named slot text.",
    "text in the default slot": "Slotted body: default slot text.",
    "an empty slot's own content": "Placeholder: no node is assigned here.",
    "an open shadow root": "Shadowed: text in an open shadow root.",
    "a nested shadow root": "Nested: text two shadow roots deep.",
    "light text after the host": "After the host: light text.",
    "a same-origin frame": "Framed: text from a same-origin frame.",
    "a shadow root inside the frame": "Framed shadow: a shadow root inside the frame.",
}

#: Not printed: a light child no slot shows, a filled slot's placeholder, and
#: elements hidden inside a shadow root and a frame.
NOT_SHOWN = {
    "an unslotted light child": "Unslotted: never rendered.",
    "a filled named slot's placeholder": "Named placeholder, not shown.",
    "a filled default slot's placeholder": "Default placeholder, not shown.",
    "a hidden element in a shadow root": "Hidden in shadow: display none.",
    "a hidden frame": "Hidden frame: display none.",
}

#: Printed, and out of any page script's reach.
UNREADABLE = {
    "an opaque (data: URL) frame": "Opaque: a data URL frame.",
    "a closed shadow root": "Closed: text in a closed shadow root.",
}


def _flat(text: str) -> str:
    """Whitespace collapsed, so a line wrap in the text layer is not a miss."""
    return " ".join(text.split())


@pytest.fixture(scope="module")
def capture(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    directory = tmp_path_factory.mktemp("shadow-frames")
    output = directory / "capture.pdf"
    report = directory / "report.json"
    code = main(
        [str(FIXTURE), "--output", str(output), "--no-ocr", "--report", str(report)]
    )
    assert code == 0
    bundle = output.with_suffix(".ai")
    chunks = [
        json.loads(line)
        for line in (bundle / "chunks.jsonl").read_text("utf-8").splitlines()
    ]
    return {
        "bundle": bundle,
        "report": json.loads(report.read_text("utf-8")),
        "surfaces": {
            "pdf text layer": _flat(
                "\n".join(p.extract_text() or "" for p in PdfReader(output).pages)
            ),
            **{
                name: _flat((bundle / name).read_text("utf-8"))
                for name in ("content.md", "content.txt", "content.html")
            },
            "chunks.jsonl": _flat("\n".join(chunk["text"] for chunk in chunks)),
        },
    }


@pytest.mark.browser
def test_the_pdf_prints_exactly_what_the_fixture_says_it_does(
    capture: dict[str, Any],
) -> None:
    """The control. Without it, the absences below could be a lost page."""
    printed = capture["surfaces"]["pdf text layer"]
    missing = {k: v for k, v in {**READABLE, **UNREADABLE}.items() if v not in printed}
    shown = {k: v for k, v in NOT_SHOWN.items() if v in printed}
    assert not missing, f"the PDF does not print: {missing}\n{printed}"
    assert not shown, f"the PDF prints what the fixture hides: {shown}"


@pytest.mark.browser
@pytest.mark.parametrize(
    "surface", ["content.md", "content.txt", "content.html", "chunks.jsonl"]
)
def test_every_text_surface_carries_what_the_pdf_prints(
    capture: dict[str, Any], surface: str
) -> None:
    text = capture["surfaces"][surface]
    missing = {k: v for k, v in READABLE.items() if v not in text}
    assert not missing, f"{surface} lacks text the PDF prints: {missing}\n{text}"
    leaked = {k: v for k, v in {**NOT_SHOWN, **UNREADABLE}.items() if v in text}
    assert not leaked, f"{surface} holds text it cannot have read: {leaked}"


@pytest.mark.browser
def test_shadow_content_sits_where_the_host_renders_it(capture: dict[str, Any]) -> None:
    """Flat-tree order, not "the host's light children, then its shadow root"."""
    markdown = capture["surfaces"]["content.md"]
    positions = [markdown.index(sentence) for sentence in READABLE.values()]
    assert positions == sorted(positions), dict(zip(READABLE, positions, strict=True))


@pytest.mark.browser
def test_what_could_not_be_read_is_named_in_the_manifest(
    capture: dict[str, Any],
) -> None:
    warnings: list[str] = capture["report"]["warnings"]
    manifest = json.loads((capture["bundle"] / "manifest.json").read_text("utf-8"))
    assert manifest["warnings"] == warnings
    frames = [w for w in warnings if "could not be read" in w]
    closed = [w for w in warnings if "closed shadow root" in w]
    # One of each, and only the one that could not be read: the readable and
    # the hidden frame are not warned about.
    assert len(frames) == 1, warnings
    assert '("Opaque frame")' in frames[0]
    assert "Its screenshot is asset-" in frames[0]
    assert len(closed) == 1, warnings
    assert "`div#sealed`" in closed[0]


@pytest.mark.browser
def test_no_password_reaches_any_artifact(capture: dict[str, Any]) -> None:
    """`source/` is the user's own input, preserved byte for byte (test_redaction)."""
    bundle: Path = capture["bundle"]
    artifacts = [
        path
        for path in sorted(bundle.rglob("*"))
        if path.is_file() and "source" not in path.relative_to(bundle).parts
    ]
    assert artifacts
    leaks = [
        f"{path.relative_to(bundle)}: {password}"
        for path in artifacts
        for password in (SHADOW_PASSWORD, FRAME_PASSWORD)
        if password in path.read_text("utf-8", errors="ignore")
    ]
    assert not leaks, leaks


@pytest.mark.browser
def test_the_passwords_were_there_to_redact(capture: dict[str, Any]) -> None:
    """Absence is only a measurement if the fields were captured at all."""
    bundle: Path = capture["bundle"]
    snapshot = (bundle / "accessibility.yaml").read_text("utf-8")
    assert 'textbox "Shadow password"' in snapshot
    assert 'textbox "Frame password"' in snapshot
    # The top document holds none, so every marker came from a shadow root or
    # a frame.
    assert snapshot.count(PASSWORD_REDACTION) == 2
    fields = {
        field["name"]: field
        for field in json.loads((bundle / "assets.json").read_text("utf-8"))[
            "form_fields"
        ]
    }
    assert fields["shadow_password"]["value"] == PASSWORD_REDACTION
    assert fields["frame_password"]["value"] == PASSWORD_REDACTION
    assert capture["surfaces"]["content.html"].count(PASSWORD_REDACTION) == 2


@pytest.mark.browser
def test_shadow_records_agree_with_the_snapshot(capture: dict[str, Any]) -> None:
    """The live value, in both places, as for a light-DOM control (P8-72)."""
    bundle: Path = capture["bundle"]
    fields = {
        field["name"]: field["value"]
        for field in json.loads((bundle / "assets.json").read_text("utf-8"))[
            "form_fields"
        ]
    }
    assert fields["shadow_note"] == "live shadow note"
    assert 'value="live shadow note"' in (bundle / "content.html").read_text("utf-8")
    links = json.loads((bundle / "links.json").read_text("utf-8"))
    assert {
        "text": "A link in the shadow root",
        "url": "https://example.org/shadow-link",
    } in links


@pytest.mark.browser
def test_a_page_cannot_spoof_an_asset_id_from_a_shadow_root(
    capture: dict[str, Any],
) -> None:
    """The harvest strips page-authored markers from every tree it reaches,
    and the image then carries an id of its own (P16-9)."""
    html = (capture["bundle"] / "content.html").read_text("utf-8")
    assert "asset-999" not in html
    records = json.loads((capture["bundle"] / "assets.json").read_text("utf-8"))
    (target,) = [a for a in records["visual_assets"] if a["alt"] == "Spoof target"]
    assert f'src="assets/{target["id"]}.png"' in html


def _assets_by_title(bundle: Path) -> dict[str, dict[str, Any]]:
    records = json.loads((bundle / "assets.json").read_text("utf-8"))
    return {asset["title"]: asset for asset in records["visual_assets"]}


@pytest.mark.browser
def test_each_frame_asset_records_which_path_its_content_took(
    capture: dict[str, Any],
) -> None:
    """Spec §5.6's per-asset provenance, which was a sentence and not a field."""
    records = json.loads((capture["bundle"] / "assets.json").read_text("utf-8"))
    frames = {
        asset["title"]: asset["frame_content"]
        for asset in records["visual_assets"]
        if asset["kind"] == "iframe"
    }
    assert frames == {"Readable frame": "dom", "Opaque frame": "screenshot"}
    others = [a for a in records["visual_assets"] if a["kind"] != "iframe"]
    # The shadow root's image, harvested since P16-9: the null is checked on
    # a record that exists.
    assert [a["alt"] for a in others] == ["Spoof target"]
    assert all(a["frame_content"] is None for a in others)


@pytest.mark.browser
def test_an_unread_frame_stands_in_the_snapshot_as_its_screenshot(
    capture: dict[str, Any],
) -> None:
    """As an <svg> does. A frame that was read stays its document (P16-7)."""
    assets = _assets_by_title(capture["bundle"])
    opaque, readable = assets["Opaque frame"]["id"], assets["Readable frame"]["id"]
    html = (capture["bundle"] / "content.html").read_text("utf-8")
    assert (
        f'<img src="assets/{opaque}.png" data-webshot-asset-id="{opaque}" '
        'alt="Opaque frame">'
    ) in html
    assert f'src="assets/{readable}.png"' not in html
    assert "<iframe" not in html


@pytest.mark.browser
def test_only_a_frame_that_was_not_read_is_warned_about(
    capture: dict[str, Any],
) -> None:
    """Every iframe asset had a warning that its content "may only be
    available through the screenshot", the readable frame's included."""
    warnings: list[str] = capture["report"]["warnings"]
    readable = _assets_by_title(capture["bundle"])["Readable frame"]["id"]
    assert not [w for w in warnings if "is an iframe" in w], warnings
    assert not [w for w in warnings if readable in w], warnings


@pytest.fixture(scope="module")
def capture_with_ocr(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """The fixture again, recognized. `--require-ocr` makes an OCR that did
    not run a failed capture, not a pass over an empty asset."""
    directory = tmp_path_factory.mktemp("shadow-frames-ocr")
    output = directory / "capture.pdf"
    report = directory / "report.json"
    code = main(
        [
            str(FIXTURE),
            "--output",
            str(output),
            "--ocr-engine",
            "tesseract",
            "--require-ocr",
            "--report",
            str(report),
        ]
    )
    assert code == 0
    return {
        "bundle": output.with_suffix(".ai"),
        "report": json.loads(report.read_text("utf-8")),
    }


@pytest.mark.browser
@pytest.mark.skipif(tesseract_executable() is None, reason="tesseract is not installed")
@pytest.mark.parametrize("surface", ["content.md", "content.txt", "chunks.jsonl"])
def test_an_unread_frame_reaches_every_text_surface_as_recognized_text(
    capture_with_ocr: dict[str, Any], surface: str
) -> None:
    """Before P16-7 the text OCR found in the frame reached assets.json only.

    Asserted as a derivation, the asset's own OCR text, not as the sentence
    this machine's Tesseract happens to return (P7-13).
    """
    bundle: Path = capture_with_ocr["bundle"]
    opaque = _assets_by_title(bundle)["Opaque frame"]
    recognized = _flat(opaque["ocr"]["text"])
    # The derivation proves nothing about an empty read.
    assert "Opaque" in recognized, opaque["ocr"]
    if surface == "chunks.jsonl":
        chunks = [
            json.loads(line)
            for line in (bundle / surface).read_text("utf-8").splitlines()
        ]
        figures = [
            chunk
            for chunk in chunks
            if chunk["meta"]["kind"] == "figure"
            and f"Visual asset {opaque['id']}" in chunk["text"]
        ]
        assert len(figures) == 1, chunks
        assert recognized in _flat(figures[0]["text"])
    else:
        assert recognized in _flat((bundle / surface).read_text("utf-8"))


@pytest.mark.browser
@pytest.mark.skipif(tesseract_executable() is None, reason="tesseract is not installed")
def test_the_warning_says_the_frame_is_there_as_recognized_text(
    capture_with_ocr: dict[str, Any],
) -> None:
    opaque = _assets_by_title(capture_with_ocr["bundle"])["Opaque frame"]["id"]
    frames = [
        w for w in capture_with_ocr["report"]["warnings"] if "could not be read" in w
    ]
    assert len(frames) == 1, frames
    assert f"content.html holds its screenshot, {opaque}, in its place" in frames[0]
    assert "hold the text OCR recognized there, not the frame's own" in frames[0]


@pytest.mark.browser
def test_a_frame_in_a_shadow_root_cannot_claim_another_assets_screenshot(
    tmp_path: Path,
) -> None:
    """The id on it is the page's. Taken from the live frame before the harvest
    reached shadow roots, it named the top frame's asset as this one's
    (P16-7). Since P16-9 the frame is an asset itself, under its own id."""
    output = tmp_path / "capture.pdf"
    report = tmp_path / "report.json"
    code = main(
        [
            str(SPOOF_FIXTURE),
            "--output",
            str(output),
            "--no-ocr",
            "--report",
            str(report),
        ]
    )
    assert code == 0
    assets = _assets_by_title(output.with_suffix(".ai"))
    # The id the spoofing frame carries is a real asset's, or this proves little.
    assert assets["Top frame"]["id"] == "asset-001"
    assert assets["Top frame"]["frame_content"] == "dom"
    warnings = json.loads(report.read_text("utf-8"))["warnings"]
    frames = [w for w in warnings if "could not be read" in w]
    assert len(frames) == 1, warnings
    assert '("Spoofing frame")' in frames[0]
    spoofing = assets["Spoofing frame"]
    assert spoofing["id"] != "asset-001"
    assert spoofing["frame_content"] == "screenshot"
    assert f"Its screenshot is {spoofing['id']};" in frames[0]
    assert "asset-001" not in frames[0]


@pytest.mark.browser
def test_every_record_locator_names_its_element(capture: dict[str, Any]) -> None:
    """` >> ` steps into the shadow root or frame document of what precedes it."""
    fields = json.loads((capture["bundle"] / "assets.json").read_text("utf-8"))[
        "form_fields"
    ]
    assert {field["name"] for field in fields} == {
        "shadow_note",
        "shadow_password",
        "frame_password",
    }
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(FIXTURE.as_uri(), wait_until="load")
            for field in fields:
                name = page.evaluate(
                    """locator => {
                        let scope = document, element = null;
                        for (const step of locator.split(' >> ')) {
                            if (element) scope = element.shadowRoot || element.contentDocument;
                            element = scope.querySelector(step);
                            if (!element) return `nothing at ${step}`;
                        }
                        return element.name;
                    }""",
                    field["locator"],
                )
                assert name == field["name"], field
        finally:
            browser.close()


# --------------------------------------------------------------------------- #
# No browser
# --------------------------------------------------------------------------- #


class _Frame:
    def __init__(self, values: list[str] | Exception) -> None:
        self.values = values

    async def evaluate(self, _script: str) -> list[str]:
        if isinstance(self.values, Exception):
            raise self.values
        return self.values


class _Page:
    def __init__(self, snapshot: str, *frames: _Frame) -> None:
        self.frames = list(frames)
        self.snapshot = snapshot

    def locator(self, _selector: str) -> Any:
        page = self

        class _Body:
            async def aria_snapshot(self, **_: Any) -> str:
                return page.snapshot

        return _Body()


def test_every_frame_is_asked_for_its_passwords() -> None:
    text = (
        f"- textbox: top\n- textbox: {SHADOW_PASSWORD}\n- textbox: {FRAME_PASSWORD}\n"
    )
    page = _Page(text, _Frame([SHADOW_PASSWORD]), _Frame([FRAME_PASSWORD]))
    scrubbed = asyncio.run(_redact_password_values(cast(Any, page), text))
    assert SHADOW_PASSWORD not in scrubbed
    assert FRAME_PASSWORD not in scrubbed
    assert scrubbed.count(PASSWORD_REDACTION) == 2


def test_a_frame_that_cannot_be_asked_withholds_the_snapshot() -> None:
    """Published with that frame's values unscrubbed is the alternative."""
    from playwright.async_api import Error as PlaywrightError

    snapshot = f"- textbox: {FRAME_PASSWORD}\n"
    page = _Page(snapshot, _Frame([]), _Frame(PlaywrightError("Frame was detached")))
    text, warning = asyncio.run(_accessibility_snapshot(cast(Any, page)))
    assert text == ""
    assert warning is not None
    assert warning.startswith("Accessibility snapshot withheld")
    assert "Frame was detached" in warning


def _text(value: str) -> dict[str, Any]:
    return {"nodeType": 3, "nodeName": "#text", "nodeValue": value}


def _element(name: str, backend: int, **extra: Any) -> dict[str, Any]:
    return {"nodeType": 1, "nodeName": name, "backendNodeId": backend, **extra}


def _root(kind: str, *children: dict[str, Any]) -> dict[str, Any]:
    return {"nodeType": 11, "shadowRootType": kind, "children": list(children)}


def test_closed_roots_are_found_in_document_order_with_their_text() -> None:
    """Style-only and user-agent roots are not text a reader sees."""
    tree = _element(
        "#document",
        0,
        children=[
            _element(
                "DIV",
                1,
                shadowRoots=[
                    _root("closed", _element("P", 10, children=[_text("one")]))
                ],
            ),
            _element(
                "DIV",
                2,
                shadowRoots=[
                    _root("closed", _element("STYLE", 11, children=[_text("p{}")]))
                ],
            ),
            _element("INPUT", 3, shadowRoots=[_root("user-agent", _text("typed"))]),
            _element(
                "DIV",
                4,
                shadowRoots=[
                    _root(
                        "open",
                        _element(
                            "SPAN", 5, shadowRoots=[_root("closed", _text(" two "))]
                        ),
                    )
                ],
            ),
            _element(
                "IFRAME",
                6,
                contentDocument=_element(
                    "#document",
                    7,
                    children=[
                        _element(
                            "DIV", 8, shadowRoots=[_root("closed", _text("three"))]
                        ),
                    ],
                ),
            ),
            _element("DIV", 9, shadowRoots=[_root("closed", _text("   "))]),
        ],
    )
    assert list(_closed_hosts(tree)) == [1, 5, 8]


def test_text_behind_a_closed_root_counts_through_an_open_one() -> None:
    assert _holds_text(
        _root("closed", _element("X", 1, shadowRoots=[_root("open", _text("deep"))]))
    )
    assert not _holds_text(
        _root("closed", _element("TEMPLATE", 1, children=[_text("inert")]))
    )


def _asset(asset_id: str, ocr_text: str, kind: str = "iframe") -> VisualAsset:
    return VisualAsset(
        id=asset_id,
        file=f"assets/{asset_id}.png",
        kind=kind,
        width=300,
        height=150,
        ocr=OCRResult(text=ocr_text),
        frame_content="screenshot" if kind == "iframe" else None,
    )


def test_each_unread_frame_says_what_the_bundle_holds_of_it() -> None:
    frames = [
        {"locator": f"body > iframe:nth-of-type({n})", "title": title, "assetId": id_}
        for n, title, id_ in [
            (1, "A", "asset-001"),
            (2, "", "asset-002"),
            (3, "", "asset-003"),
            (4, "", ""),
        ]
    ]
    warnings = unread_text_warnings(
        {"unreadFrames": frames, "closedShadowHosts": []},
        [
            _asset("asset-001", "recognized"),
            _asset("asset-002", "recognized"),
            _asset("asset-003", ""),
        ],
        # asset-002's picture was not attached: the count check skipped it.
        pictured={"asset-001", "asset-003"},
    )
    assert len(warnings) == 4
    assert '`body > iframe:nth-of-type(1)` ("A")' in warnings[0]
    assert (
        "content.html holds its screenshot, asset-001, in its place, and the "
        "content.md, content.txt and chunks.jsonl built from it hold the text OCR "
        "recognized there, not the frame's own." in warnings[0]
    )
    assert "Its text is in the PDF but not in content.html" not in warnings[0]
    assert (
        "Text recognized in its screenshot, asset-002, is in assets.json only."
        in warnings[1]
    )
    assert "Its text is in the PDF but not in content.html" in warnings[1]
    # Pictured, with nothing recognized: no text surface holds any of it.
    assert (
        "Its screenshot is asset-003; assets.json holds no recognized text"
        in warnings[2]
    )
    assert "Its text is in the PDF but not in content.html" in warnings[2]
    assert "It was not captured as a visual asset either." in warnings[3]


def test_a_frame_is_dom_only_when_the_snapshot_read_its_document() -> None:
    """`screenshot` is what the harvest took; the snapshot upgrades what it read."""
    assets = [
        _asset("asset-001", ""),
        _asset("asset-002", ""),
        _asset("asset-003", "", kind="img"),
    ]
    marked = with_frame_content({"readFrameAssets": ["asset-001", "asset-003"]}, assets)
    assert [asset.frame_content for asset in marked] == ["dom", "screenshot", None]
    # A copy: the harvest's records are not rewritten behind its back.
    assert assets[0].frame_content == "screenshot"


def test_a_closed_root_check_that_did_not_run_says_so() -> None:
    """None is "could not look", which is not the same as "found none"."""
    ran = unread_text_warnings(
        {"unreadFrames": [], "closedShadowHosts": []}, [], pictured=()
    )
    assert ran == []
    failed = unread_text_warnings(
        {"unreadFrames": [], "closedShadowHosts": None}, [], pictured=()
    )
    assert len(failed) == 1
    assert failed[0].startswith("Closed shadow roots could not be checked")
    found = unread_text_warnings(
        {"unreadFrames": [], "closedShadowHosts": ["div#a", "x-card >> div#b"]},
        [],
        pictured=(),
    )
    assert found[0].startswith(
        "2 closed shadow root(s) hold text, on `div#a`, `x-card >> div#b`."
    )


class _Session:
    """The DevTools calls `_closed_shadow_hosts` makes, answered from a script."""

    def __init__(self, located: list[dict[str, Any]]) -> None:
        self.located = located
        self.sent: list[str] = []

    async def send(
        self, method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self.sent.append(method)
        params = params or {}
        if method == "DOM.getDocument":
            hosts = [
                _element("DIV", backend, shadowRoots=[_root("closed", _text("x"))])
                for backend in range(1, len(self.located) + 1)
            ]
            return {"root": _element("#document", 0, children=hosts)}
        if method == "DOM.resolveNode":
            return {"object": {"objectId": str(params["backendNodeId"])}}
        if method == "Runtime.callFunctionOn":
            return self.located[int(params["objectId"]) - 1]
        return {}

    async def detach(self) -> None:
        raise AssertionError("detaching resets the page's emulated media")


def _cdp_page(session: _Session | Exception) -> Any:
    class _Context:
        async def new_cdp_session(self, _page: Any) -> _Session:
            if isinstance(session, Exception):
                raise session
            return session

    class _CdpPage:
        context = _Context()

    return _CdpPage()


def test_a_host_in_an_unreachable_frame_is_left_to_the_frame_warning() -> None:
    session = _Session(
        [
            {"result": {"type": "string", "value": "div#a"}},
            {"result": {"type": "object", "subtype": "null", "value": None}},
            {"result": {"type": "string", "value": "iframe >> div#b"}},
        ]
    )
    hosts = asyncio.run(_closed_shadow_hosts(_cdp_page(session)))
    assert hosts == ["div#a", "iframe >> div#b"]
    # The session stays open, so what the check turned on is turned off.
    assert session.sent[-2:] == ["Runtime.releaseObjectGroup", "DOM.disable"]


def test_a_locator_that_threw_fails_the_check_rather_than_skipping_the_host() -> None:
    session = _Session(
        [
            {"result": {"type": "string", "value": "div#a"}},
            {"result": {"type": "object"}, "exceptionDetails": {"text": "Uncaught"}},
        ]
    )
    assert asyncio.run(_closed_shadow_hosts(_cdp_page(session))) is None
    assert session.sent[-1] == "DOM.disable"


def test_no_devtools_session_means_the_check_did_not_run() -> None:
    from playwright.async_api import Error as PlaywrightError

    page = _cdp_page(PlaywrightError("CDP session is only available in Chromium"))
    assert asyncio.run(_closed_shadow_hosts(page)) is None


# --------------------------------------------------------------------------- #
# DevTools and the emulated media
# --------------------------------------------------------------------------- #

_PRINTING = "matchMedia('print').matches"


@pytest.mark.browser
def test_detaching_a_devtools_session_resets_the_emulated_media() -> None:
    """The control: why `devtools()` never detaches.

    If this starts failing, Chromium stopped resetting the media type on
    detach, and the reason for keeping every session open is gone.
    """

    async def run() -> tuple[bool, bool]:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.set_content("<p>Printed.</p>")
                await page.emulate_media(media="print")
                before = await page.evaluate(_PRINTING)
                session = await page.context.new_cdp_session(page)
                await session.detach()
                return before, await page.evaluate(_PRINTING)
            finally:
                await browser.close()

    assert asyncio.run(run()) == (True, False)


@pytest.mark.browser
def test_asking_devtools_leaves_the_page_printing(tmp_path: Path) -> None:
    """Both questions WebShot asks DevTools, in the context where both run.

    A persistent profile is the `--auth-profile` shape: there
    `_chromium_version` has no `Browser` to ask, and went through a session it
    then detached, before every render (docs/09 P16-6).
    """

    async def run() -> tuple[str, list[str] | None, bool, bool]:
        async with async_playwright() as playwright:
            context = await playwright.chromium.launch_persistent_context(
                str(tmp_path / "profile"), headless=True
            )
            try:
                page = await context.new_page()
                await page.goto(FIXTURE.as_uri(), wait_until="load")
                await page.emulate_media(media="print")
                version = await _chromium_version(page)
                hosts = await _closed_shadow_hosts(page)
                shared = await devtools(page) is await devtools(page)
                return version, hosts, await page.evaluate(_PRINTING), shared
            finally:
                await context.close()

    version, hosts, printing, shared = asyncio.run(run())
    assert _a_version_number(version)
    assert hosts == ["div#sealed"]
    assert printing, "a DevTools question switched the page to screen media"
    assert shared


def _a_version_number(version: str) -> bool:
    """What the session answers, where the fallback is `"unknown"`."""
    return version != "unknown" and version[0].isdigit()
