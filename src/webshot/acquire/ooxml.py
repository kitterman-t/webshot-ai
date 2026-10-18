"""Office content MarkItDown's readers pass over, put where they read it.

MarkItDown reads a deck through python-pptx and a Word file through mammoth.
Each reads a document's main structure and skips, without a word, what sits
beside it (docs/09 P27):

* **`mc:AlternateContent`.** PowerPoint wraps a shape in it whenever the shape
  holds something a 2007 reader cannot draw (an equation, most often) and
  writes it twice: the shape itself in `mc:Choice`, and in `mc:Fallback` a
  shape filled with a *picture* of it. python-pptx's shape tree reads neither
  branch, so a slide whose body text box held an inline equation came out as
  its title alone.
* **Equations** (`m:oMath`). python-pptx's text is its `a:r` runs and an
  equation is not one, so `x ≤ 12` read as `x  12`. MarkItDown converts Word's
  equations to LaTeX itself, but a whole part at a time: one equation its
  converter cannot read (a fraction with no `m:fPr`, which the schema allows)
  and it keeps none of them, and mammoth drops every one.
* **SmartArt.** A diagram's text lives in its own part
  (`ppt/diagrams/data*.xml`); the slide holds only a frame pointing at it.

So the archive is rewritten before MarkItDown opens it, the way MarkItDown
rewrites a Word file's equations itself: each `mc:AlternateContent` becomes its
Choice, each equation a text run of its LaTeX, each diagram a text box of its
points. A part holding none of these is copied byte for byte, so a document
without them reaches MarkItDown exactly as before.

What still cannot be read is said rather than dropped. An equation the LaTeX
converter refuses is given as its characters in document order, and a frame
nothing renders (an embedded object, a chart type python-pptx does not know) is
named; both come back as warnings for the manifest.

`rewrite_parts` is the same machinery for an edit the caller supplies: the
bridge uses it to write the text MarkItDown copies into Markdown as text
(docs/09 P28).

`number_word_lists` carries what mammoth reads of a list item, its level and
whether it is ordered, the rest of the way: the number Word shows on it.
mammoth wrote every `<ol>` from 1.  The number is worked out here from
`numbering.xml`, by the rules Word was measured to count by, and written into
the paragraph as a mark the bridge's HTML step turns into each list's start
(docs/09 P31).

Every part is parsed through defusedxml (spec §6.6). Nothing upstream is
imported here: the equation converter is MarkItDown's, so the bridge hands it in.
"""

from __future__ import annotations

import copy
import hashlib
import io
import posixpath
import zipfile
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import unquote
from xml.etree.ElementTree import Element, ParseError, SubElement, tostring

from defusedxml import ElementTree as DefusedElementTree
from defusedxml.common import DefusedXmlException

#: One `m:oMath` to LaTeX, raising when it cannot.
EquationConverter = Callable[[Element], str]

NAMESPACES = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "a14": "http://schemas.microsoft.com/office/drawing/2010/main",
    "ct": "http://schemas.openxmlformats.org/package/2006/content-types",
    "dgm": "http://schemas.openxmlformats.org/drawingml/2006/diagram",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "xml": "http://www.w3.org/XML/1998/namespace",
}

TABLE_URI = "http://schemas.openxmlformats.org/drawingml/2006/table"
CHART_URI = "http://schemas.openxmlformats.org/drawingml/2006/chart"
DIAGRAM_URI = NAMESPACES["dgm"]
OLE_URI = "http://schemas.openxmlformats.org/presentationml/2006/ole"
CHARTEX_URI = "http://schemas.microsoft.com/office/drawing/2014/chartex"

#: The namespaces whose `mc:Choice` is read in place of the Fallback: DrawingML
#: 2010's, where PowerPoint's equations (`a14:m`) live. A Choice that requires
#: anything else stays wrapped, as python-pptx always saw it, because unwrapped
#: it can hold what python-pptx fails on: the ink PowerPoint writes under `p14`
#: is a `p:contentPart`, and reading one raised AttributeError (docs/09 P27).
UNDERSTOOD = frozenset({NAMESPACES["a14"]})

#: The SmartArt points that carry the diagram's words. `doc` is the diagram
#: itself, `pres` a drawn shape, and the two transition types the connectors.
TEXT_POINT_TYPES = frozenset({"node", "asst"})

#: Bytes whose absence proves a part holds nothing this module rewrites or
#: reports, so it need not be parsed at all.
SLIDE_MARKERS = (b"AlternateContent", b"oMath", b"graphicFrame")
NOTES_MARKERS = (b"AlternateContent", b"oMath")


def _q(name: str) -> str:
    prefix, local = name.split(":")
    return f"{{{NAMESPACES[prefix]}}}{local}"


def _parse(payload: bytes, part: str) -> Element | None:
    """A part's root, or None when it is not well-formed XML.

    A malformed part is left for the reader to report, as `guard_xml` leaves a
    malformed file; hostile structure is refused outright.
    """
    try:
        root: Element = DefusedElementTree.fromstring(payload)
    except DefusedXmlException as exc:
        raise ValueError(
            f"{part} declares XML entities or external references, which "
            f"WebShot refuses to parse ({type(exc).__name__}; docs/04-spec.md §6.6)."
        ) from exc
    except ParseError:
        return None
    return root


def _parse_with_prefixes(
    payload: bytes, part: str
) -> tuple[Element, dict[str, set[str]]] | None:
    """A part's root, and every namespace each prefix is declared as in it.

    `mc:Choice Requires` names prefixes, and ElementTree keeps no declarations
    once it has resolved them, so they are collected as the part is read.
    """
    prefixes: dict[str, set[str]] = {}
    try:
        events = DefusedElementTree.iterparse(io.BytesIO(payload), events=("start-ns",))
        for _, (prefix, uri) in events:
            prefixes.setdefault(prefix, set()).add(uri)
    except DefusedXmlException as exc:
        raise ValueError(
            f"{part} declares XML entities or external references, which "
            f"WebShot refuses to parse ({type(exc).__name__}; docs/04-spec.md §6.6)."
        ) from exc
    except ParseError:
        return None
    root: Element = events.root
    return root, prefixes


def _member(archive: zipfile.ZipFile, part: str | zipfile.ZipInfo) -> bytes | None:
    name = part.filename if isinstance(part, zipfile.ZipInfo) else part
    if name not in archive.NameToInfo:
        return None
    try:
        return archive.read(part)
    except (zipfile.BadZipFile, zlib.error, EOFError, RuntimeError) as exc:
        raise ValueError(f"{name} could not be read from the archive: {exc}") from exc


def _open(data: bytes) -> zipfile.ZipFile | None:
    """The archive, or None when it is not one: the reader reports that."""
    try:
        return zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return None


def _read(archive: zipfile.ZipFile, part: str) -> Element | None:
    payload = _member(archive, part)
    return None if payload is None else _parse(payload, part)


def _relationships(archive: zipfile.ZipFile, part: str) -> list[tuple[str, str, str]]:
    """`(id, kind, target part)` for each internal relationship of `part`.

    `kind` is the last segment of the relationship type, so the transitional
    and strict spellings of one type compare equal.
    """
    folder, name = posixpath.split(part)
    root = _read(archive, posixpath.join(folder, "_rels", f"{name}.rels"))
    if root is None:
        return []
    found: list[tuple[str, str, str]] = []
    for relationship in root.iter(_q("rel:Relationship")):
        target = relationship.get("Target") or ""
        if relationship.get("TargetMode") == "External" or not target:
            continue
        resolved = (
            target.lstrip("/")
            if target.startswith("/")
            else posixpath.normpath(posixpath.join(folder, target))
        )
        kind = (relationship.get("Type") or "").rsplit("/", 1)[-1]
        found.append((relationship.get("Id") or "", kind, resolved))
    return found


def _main_part(archive: zipfile.ZipFile) -> str | None:
    for _, kind, target in _relationships(archive, ""):
        if kind == "officeDocument":
            return target
    return None


def _parents(root: Element) -> dict[Element, Element]:
    return {child: parent for parent in root.iter() for child in parent}


def _replace(parents: dict[Element, Element], old: Element, new: list[Element]) -> None:
    parent = parents[old]
    index = list(parent).index(old)
    parent[index : index + 1] = new


def _has_text(element: Element) -> bool:
    return any(
        (node.text or "").strip()
        for node in element.iter()
        if node.tag in (_q("a:t"), _q("m:t"))
    )


def _understood(requires: str | None, prefixes: dict[str, set[str]]) -> bool:
    tokens = (requires or "").split()
    return bool(tokens) and all(
        len(prefixes.get(token, ())) == 1 and prefixes[token] <= UNDERSTOOD
        for token in tokens
    )


def _resolve_alternate_content(
    root: Element, prefixes: dict[str, set[str]]
) -> tuple[bool, list[Element]]:
    """Replace each `mc:AlternateContent` whose Choice is understood with it.

    The Choice is what PowerPoint itself reads; the Fallback is what it writes
    for older readers, and for an equation that is a picture of the text. A
    Choice that is not understood is left wrapped and returned, so what it
    holds can be reported. Iterative, and re-examining what it put in place, so
    a nested block is resolved too and a deep document cannot exhaust the stack.
    """
    changed = False
    kept: list[Element] = []
    stack = [root]
    while stack:
        parent = stack.pop()
        index = 0
        while index < len(parent):
            child = parent[index]
            if child.tag != _q("mc:AlternateContent"):
                stack.append(child)
                index += 1
                continue
            choice = child.find(_q("mc:Choice"))
            if choice is not None and not _understood(choice.get("Requires"), prefixes):
                kept.append(choice)
                index += 1
                continue
            branch = choice if choice is not None else child.find(_q("mc:Fallback"))
            parent[index : index + 1] = list(branch) if branch is not None else []
            changed = True
    return changed, kept


def _kept_text_warnings(label: str, kept: list[Element]) -> list[str]:
    warnings = []
    for choice in kept:
        text = " ".join(
            (node.text or "").strip()
            for node in choice.iter()
            if node.tag in (_q("a:t"), _q("m:t")) and (node.text or "").strip()
        )
        if text:
            shown = text if len(text) <= 80 else f"{text[:79]}…"
            warnings.append(
                f"{label}: text PowerPoint wrote for readers of "
                f"{choice.get('Requires')!r} is not in the page ({shown!r})."
            )
    return warnings


def _equation_runs(
    container: Element, convert: EquationConverter, run: Callable[[str], Element]
) -> tuple[list[Element], list[str]]:
    """The text runs that stand for one equation, and the text of any refused.

    `$…$` inline and `$$…$$` for a display equation, as MarkItDown writes
    Word's, so an equation reads the same whichever format it arrived in.
    """
    display = container.tag == _q("m:oMathPara") or (
        container.find(_q("m:oMathPara")) is not None
    )
    equations = (
        [container]
        if container.tag == _q("m:oMath")
        else list(container.iter(_q("m:oMath")))
    )
    runs: list[Element] = []
    refused: list[str] = []
    for equation in equations:
        try:
            latex = convert(equation)
        # Any exception, not a list: the converter is third-party code reading
        # untrusted markup, and it fails with a bare KeyError on an optional
        # element it expected. A refusal costs the layout, never the text.
        except Exception:
            text = "".join(node.text or "" for node in equation.iter(_q("m:t")))
            runs.append(run(text))
            refused.append(text)
        else:
            runs.append(run(f"$${latex}$$" if display else f"${latex}$"))
    return runs, refused


def _refusal_warnings(label: str, refused: list[str]) -> list[str]:
    return [
        f"{label}: an equation could not be converted to LaTeX, so its "
        f"characters are given in document order without its layout ({text!r})."
        for text in refused
    ]


def _drawing_run(text: str) -> Element:
    run = Element(_q("a:r"))
    SubElement(run, _q("a:t")).text = text
    return run


def _word_run(text: str) -> Element:
    run = Element(_q("w:r"))
    SubElement(run, _q("w:t"), {_q("xml:space"): "preserve"}).text = text
    return run


def _slide_equations(
    root: Element, convert: EquationConverter
) -> tuple[bool, list[str]]:
    """Each `a14:m` (DrawingML's equation wrapper) becomes text runs in place.

    Not one still inside `mc:AlternateContent`: that block was left wrapped,
    python-pptx will not read it, and its text is reported as a whole.
    """
    parents = _parents(root)
    wrappers = [
        wrapper
        for wrapper in root.iter(_q("a14:m"))
        if not _under(parents, wrapper, _q("mc:AlternateContent"))
    ]
    refused: list[str] = []
    for wrapper in wrappers:
        runs, failed = _equation_runs(wrapper, convert, _drawing_run)
        _replace(parents, wrapper, runs)
        refused.extend(failed)
    return bool(wrappers), refused


def _diagram_paragraphs(model: Element) -> list[Element]:
    """A SmartArt data model's paragraphs, in the diagram's own order.

    The order is the `parOf` hierarchy read from the document point down,
    siblings by `srcOrd`, which is the outline PowerPoint's text pane shows. A
    point the hierarchy does not reach is still read, after the rest, in the
    order it is stored: unordered text is better than missing text.
    """
    points = list(model.iter(_q("dgm:pt")))
    text_points = {
        point.get("modelId"): point
        for point in points
        if point.get("type", "node") in TEXT_POINT_TYPES
    }
    children: dict[str | None, list[tuple[int, str | None]]] = {}
    for connection in model.iter(_q("dgm:cxn")):
        if connection.get("type", "parOf") != "parOf":
            continue
        order = connection.get("srcOrd") or "0"
        children.setdefault(connection.get("srcId"), []).append(
            (int(order) if order.isdigit() else 0, connection.get("destId"))
        )
    ordered: list[Element] = []
    seen: set[str | None] = set()
    stack = [
        point.get("modelId") for point in reversed(points) if point.get("type") == "doc"
    ]
    while stack:
        model_id = stack.pop()
        if model_id in seen:
            continue
        seen.add(model_id)
        if model_id in text_points:
            ordered.append(text_points[model_id])
        # Reversed onto a stack, so the lowest `srcOrd` is read first and
        # siblings that share one keep their stored order.
        siblings = sorted(children.get(model_id, []), key=lambda child: child[0])
        stack.extend(dest for _, dest in reversed(siblings))
    ordered.extend(point for key, point in text_points.items() if key not in seen)
    return [
        copy.deepcopy(paragraph)
        for point in ordered
        for paragraph in point.iterfind(f"{_q('dgm:t')}/{_q('a:p')}")
        if _has_text(paragraph)
    ]


def _text_box(frame: Element, paragraphs: list[Element]) -> Element:
    """A plain text box where `frame` was, so the reader keeps its place."""
    shape = Element(_q("p:sp"))
    names = SubElement(shape, _q("p:nvSpPr"))
    source = frame.find(f"{_q('p:nvGraphicFramePr')}/{_q('p:cNvPr')}")
    SubElement(
        names,
        _q("p:cNvPr"),
        dict(source.attrib) if source is not None else {"id": "0", "name": "Diagram"},
    )
    SubElement(names, _q("p:cNvSpPr"), {"txBox": "1"})
    SubElement(names, _q("p:nvPr"))
    properties = SubElement(shape, _q("p:spPr"))
    placement = frame.find(_q("p:xfrm"))
    if placement is not None:
        SubElement(properties, _q("a:xfrm")).extend(
            copy.deepcopy(child) for child in placement
        )
    body = SubElement(shape, _q("p:txBody"))
    SubElement(body, _q("a:bodyPr"))
    SubElement(body, _q("a:lstStyle"))
    body.extend(paragraphs)
    return shape


def _slide_diagrams(
    root: Element, archive: zipfile.ZipFile, targets: dict[str, str], label: str
) -> tuple[bool, list[str]]:
    changed = False
    warnings: list[str] = []
    parents = _parents(root)
    for frame in list(root.iter(_q("p:graphicFrame"))):
        data = frame.find(f"{_q('a:graphic')}/{_q('a:graphicData')}")
        if data is None or data.get("uri") != DIAGRAM_URI:
            continue
        if _under(parents, frame, _q("mc:AlternateContent")):
            continue  # left wrapped, and named by `_unread_frames`
        ids = data.find(_q("dgm:relIds"))
        target = targets.get(ids.get(_q("r:dm")) or "") if ids is not None else None
        model = _read(archive, target) if target else None
        if model is None:
            warnings.append(
                f"{label}: a SmartArt diagram's text could not be read, so it "
                "is not in the page."
            )
            continue
        paragraphs = _diagram_paragraphs(model)
        if paragraphs:
            _replace(parents, frame, [_text_box(frame, paragraphs)])
            changed = True
    return changed, warnings


def _unread_frames(root: Element, label: str) -> list[str]:
    """Name each graphic frame MarkItDown renders nothing for.

    python-pptx gives MarkItDown tables and charts, unless they sit in a Choice
    left wrapped, which it never reads. A SmartArt frame still here held no
    text, or was reported when its text could not be read. A Fallback is not
    looked in: it is the Choice again, as a picture.
    """
    warnings: list[str] = []
    stack = [(root, False)]
    while stack:
        element, wrapped = stack.pop()
        if element.tag == _q("mc:Fallback"):
            continue
        wrapped = wrapped or element.tag == _q("mc:Choice")
        stack.extend((child, wrapped) for child in reversed(element))
        if element.tag != _q("a:graphicData"):
            continue
        uri = element.get("uri") or ""
        if uri in (TABLE_URI, CHART_URI, DIAGRAM_URI) and not wrapped:
            continue
        if uri == OLE_URI:
            # PowerPoint wraps the object itself, inside the frame, so it is
            # looked for at any depth; Choice and Fallback both name it.
            embedded = next(element.iter(_q("p:oleObj")), None)
            program = embedded.get("progId") if embedded is not None else None
            what = (
                f"an embedded object ({program})" if program else "an embedded object"
            )
        elif uri == CHARTEX_URI:
            what = "a chart of a type python-pptx cannot read"
        elif uri == TABLE_URI:
            what = "a table PowerPoint wrapped for newer readers"
        elif uri == CHART_URI:
            what = "a chart PowerPoint wrapped for newer readers"
        elif uri == DIAGRAM_URI:
            what = "a SmartArt diagram PowerPoint wrapped for newer readers"
        else:
            what = f"a graphic frame of type {uri or 'unknown'}"
        warnings.append(f"{label}: {what} is not in the page.")
    return warnings


#: Frames mammoth passes over without a word: it reads pictures, and text boxes
#: through their VML Fallback, and nothing else a drawing can hold.
WORD_UNREAD_FRAMES = {
    CHART_URI: "a chart",
    CHARTEX_URI: "a chart",
    DIAGRAM_URI: "a SmartArt diagram",
}


def _word_frames(root: Element, label: str) -> list[str]:
    """Name each Word chart or SmartArt diagram, which mammoth drops.

    A Fallback is not looked in, so a frame PowerPoint-style wrapping wrote
    twice is named once.
    """
    warnings: list[str] = []
    stack = [root]
    while stack:
        element = stack.pop()
        if element.tag == _q("mc:Fallback"):
            continue
        stack.extend(reversed(element))
        if element.tag == _q("a:graphicData"):
            what = WORD_UNREAD_FRAMES.get(element.get("uri") or "")
            if what:
                warnings.append(f"{label}: {what} is not in the page.")
    return warnings


def _serialize(root: Element) -> bytes:
    serialized: bytes = tostring(root, encoding="utf-8", xml_declaration=True)
    return serialized


def _rebuild(data: bytes, replacements: dict[str, bytes]) -> bytes:
    """The archive again, with `replacements` swapped in and all else as it was."""
    if not replacements:
        return data
    output = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(data)) as source,
        zipfile.ZipFile(output, "w") as target,
    ):
        for info in source.infolist():
            payload = replacements.get(info.filename)
            if payload is None:
                payload = _member(source, info) or b""
            # A fresh entry rather than the source's: its extra field and flags
            # describe the bytes as they were stored there, not as written here.
            # Stored, not compressed: the archive is read once, in memory, and
            # deflating a document's pictures again made one Word file with a
            # list 15 times slower to read (docs/09 P31).
            entry = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            entry.compress_type = zipfile.ZIP_STORED
            entry.external_attr = info.external_attr
            entry.create_system = info.create_system
            target.writestr(entry, payload)
    return output.getvalue()


def _part_types(archive: zipfile.ZipFile) -> dict[str, str]:
    """Each member's content type, as `[Content_Types].xml` declares it.

    That declaration, not the part's name, is what python-pptx and openpyxl
    choose a part's reader by.
    """
    declared = _read(archive, "[Content_Types].xml")
    if declared is None:
        return {}
    defaults = {
        (entry.get("Extension") or "").lower(): entry.get("ContentType") or ""
        for entry in declared.iter(_q("ct:Default"))
    }
    overrides = {
        unquote(entry.get("PartName") or "").lstrip("/").lower(): (
            entry.get("ContentType") or ""
        )
        for entry in declared.iter(_q("ct:Override"))
    }
    types: dict[str, str] = {}
    for name in archive.namelist():
        key = name.lower()
        kind = overrides.get(key) or defaults.get(posixpath.splitext(key)[1][1:])
        if kind:
            types[name] = kind
    return types


def rewrite_parts(data: bytes, rewrites: dict[str, Callable[[Element], bool]]) -> bytes:
    """`data` with each part of a content type in `rewrites` passed through it.

    A rewrite edits the part's tree in place and says whether it changed
    anything.  A part it leaves alone is copied byte for byte, and a package in
    which it changed nothing is returned as it came.
    """
    archive = _open(data)
    if archive is None:
        return data
    replacements: dict[str, bytes] = {}
    with archive:
        for part, kind in _part_types(archive).items():
            rewrite = rewrites.get(kind)
            root = _read(archive, part) if rewrite else None
            if rewrite and root is not None and rewrite(root):
                replacements[part] = _serialize(root)
    return _rebuild(data, replacements)


def _slide_parts(archive: zipfile.ZipFile, presentation: str) -> list[str]:
    """The slide parts in presentation order, which is the order MarkItDown numbers."""
    root = _read(archive, presentation)
    if root is None:
        return []
    targets = {rid: target for rid, _, target in _relationships(archive, presentation)}
    return [
        targets[rid]
        for slide in root.iter(_q("p:sldId"))
        if (rid := slide.get(_q("r:id")) or "") in targets
    ]


def prepare_pptx(data: bytes, convert: EquationConverter) -> tuple[bytes, list[str]]:
    """A deck MarkItDown reads whole, and what it still cannot read."""
    replacements: dict[str, bytes] = {}
    warnings: list[str] = []
    archive = _open(data)
    if archive is None:
        return data, []
    with archive:
        presentation = _main_part(archive)
        slides = _slide_parts(archive, presentation) if presentation else []
        for number, slide in enumerate(slides, start=1):
            relationships = _relationships(archive, slide)
            label = f"Slide {number}"
            payload = _member(archive, slide)
            parsed = (
                _parse_with_prefixes(payload, slide)
                if payload and any(marker in payload for marker in SLIDE_MARKERS)
                else None
            )
            if parsed is not None:
                root, prefixes = parsed
                resolved, kept = _resolve_alternate_content(root, prefixes)
                targets = {rid: target for rid, _, target in relationships}
                diagrams, unreadable = _slide_diagrams(root, archive, targets, label)
                equations, refused = _slide_equations(root, convert)
                warnings += unreadable + _refusal_warnings(label, refused)
                warnings += _kept_text_warnings(label, kept)
                warnings += _unread_frames(root, label)
                if resolved or diagrams or equations:
                    replacements[slide] = _serialize(root)
            for notes in (t for _, kind, t in relationships if kind == "notesSlide"):
                payload = _member(archive, notes)
                if not payload or not any(m in payload for m in NOTES_MARKERS):
                    continue
                parsed = _parse_with_prefixes(payload, notes)
                if parsed is None:
                    continue
                root, prefixes = parsed
                resolved, kept = _resolve_alternate_content(root, prefixes)
                equations, refused = _slide_equations(root, convert)
                notes_label = f"The notes of slide {number}"
                warnings += _refusal_warnings(notes_label, refused)
                warnings += _kept_text_warnings(notes_label, kept)
                if resolved or equations:
                    replacements[notes] = _serialize(root)
    return _rebuild(data, replacements), warnings


def prepare_docx(data: bytes, convert: EquationConverter) -> tuple[bytes, list[str]]:
    """A Word file whose equations are text runs, each converted on its own.

    MarkItDown's own pass then finds no equation to convert. An equation inside
    `mc:Choice` is converted but not reported: mammoth reads the Fallback, which
    holds the same equation and is reported in its place. Charts and SmartArt
    are named, not read (docs/09 P27).
    """
    replacements: dict[str, bytes] = {}
    warnings: list[str] = []
    archive = _open(data)
    if archive is None:
        return data, []
    with archive:
        document = _main_part(archive)
        if document is None:
            return data, []
        parts = [(document, "The document body")] + [
            (target, f"The {kind}")
            for _, kind, target in _relationships(archive, document)
            if kind in ("footnotes", "endnotes")
        ]
        for part, label in parts:
            payload = _member(archive, part)
            if not payload or not (b"oMath" in payload or b"graphicData" in payload):
                continue
            root = _parse(payload, part)
            if root is None:
                continue
            warnings += _word_frames(root, label)
            parents = _parents(root)
            containers = list(root.iter(_q("m:oMathPara"))) + [
                equation
                for equation in root.iter(_q("m:oMath"))
                if parents[equation].tag != _q("m:oMathPara")
            ]
            for container in containers:
                runs, refused = _equation_runs(container, convert, _word_run)
                if not _under(parents, container, _q("mc:Choice")):
                    warnings += _refusal_warnings(label, refused)
                _replace(parents, container, runs)
            if containers:
                replacements[part] = _serialize(root)
    return _rebuild(data, replacements), warnings


def _under(parents: dict[Element, Element], element: Element, tag: str) -> bool:
    node = parents.get(element)
    while node is not None:
        if node.tag == tag:
            return True
        node = parents.get(node)
    return False


# --------------------------------------------------------------------------- #
# Word list numbers (docs/09 P31)
# --------------------------------------------------------------------------- #

#: What opens and closes the mark a numbered paragraph carries through mammoth.
#: Private-use characters, which mammoth copies as text; between them, the
#: document's digest and the paragraph's index in `WordListNumbers.values`.
LIST_MARK_OPEN = "\ue000"
LIST_MARK_CLOSE = "\ue001"
#: How deep a style's `w:basedOn` chain, or a list's `w:numStyleLink`, is
#: followed before it is taken for the cycle it most likely is.
_STYLE_DEPTH = 32


@dataclass(slots=True)
class WordListNumbers:
    """The numbers Word shows on a document's list items, and how they fared.

    `values` holds one entry per marked paragraph, in document order: the
    number Word shows on it, or None where Word shows none.  The counts are
    filled in by whatever writes the page from mammoth's HTML, which is also
    what sets `applied`.
    """

    mark: str
    values: list[int | None]
    applied: bool = False
    #: Ordered list items carrying no mark: Word's number for them is unknown.
    unmatched: int = 0
    #: Items Word shows without a number, which the page numbers all the same.
    unnumbered: int = 0
    #: Paragraphs Word numbers that the page does not show as list items.
    unlisted: int = 0

    def item_mark(self, index: int) -> str:
        return f"{self.mark}{index}{LIST_MARK_CLOSE}"


@dataclass(frozen=True, slots=True)
class _ListLevel:
    start: int | None  # None when `w:start` is there but no number
    ordered: bool  # mammoth's test: any `w:numFmt` but `bullet`
    shown: bool  # `w:numFmt="none"` counts but shows nothing
    restart: int | None  # `w:lvlRestart`, absent as None


@dataclass(frozen=True, slots=True)
class _ListInstance:
    abstract: str
    #: `w:startOverride` by level; None where it holds no number.
    overrides: dict[int, int | None]


def _value(element: Element | None) -> str | None:
    return None if element is None else element.get(_q("w:val"))


def _number(element: Element | None, attribute: str = "w:val") -> int | None:
    """An ST_DecimalNumber attribute, or None when it holds no number."""
    if element is None:
        return None
    try:
        return int(element.get(_q(attribute)) or "")
    except ValueError:
        return None


def _list_levels(abstract: Element) -> dict[int, _ListLevel]:
    levels: dict[int, _ListLevel] = {}
    for level in abstract.findall(_q("w:lvl")):
        # mammoth reads a level without `w:ilvl` as level 0, as Word does.
        index = _number(level, "w:ilvl") if level.get(_q("w:ilvl")) else 0
        if index is None:
            continue
        fmt_element = level.find(_q("w:numFmt"))
        fmt = None if fmt_element is None else fmt_element.get(_q("w:val"))
        start_element = level.find(_q("w:start"))
        restart_element = level.find(_q("w:lvlRestart"))
        # Absent, the start is 0 (ISO/IEC 29500-1 §17.9.26), and Word counts from 0.
        start = 0 if start_element is None else _number(start_element)
        levels.setdefault(
            index,
            _ListLevel(
                start=start,
                ordered=fmt != "bullet",
                # Word shows no number on any item of a level that starts
                # below 0, though it counts them (docs/09 P31).
                shown=fmt != "none" and (start is None or start >= 0),
                restart=None if restart_element is None else _number(restart_element),
            ),
        )
    return levels


def _style_numbering(
    styles: Element | None,
) -> tuple[dict[str, tuple[str | None, int | None]], str | None]:
    """Each style's `w:numPr`, through `w:basedOn`, and the default paragraph style."""
    own: dict[str, tuple[str | None, int | None]] = {}
    based_on: dict[str, str] = {}
    default: str | None = None
    for style in [] if styles is None else styles.findall(_q("w:style")):
        style_id = style.get(_q("w:styleId")) or ""
        numbering = style.find(f"{_q('w:pPr')}/{_q('w:numPr')}")
        if numbering is not None:
            num_id = numbering.find(_q("w:numId"))
            own[style_id] = (
                None if num_id is None else num_id.get(_q("w:val")),
                _number(numbering.find(_q("w:ilvl"))),
            )
        parent = style.find(_q("w:basedOn"))
        if parent is not None:
            based_on[style_id] = parent.get(_q("w:val")) or ""
        if style.get(_q("w:type")) == "paragraph" and style.get(_q("w:default")) in (
            "1",
            "true",
            "on",
        ):
            default = style_id
    resolved: dict[str, tuple[str | None, int | None]] = {}
    for style_id in set(own) | set(based_on):
        current: str | None = style_id
        for _ in range(_STYLE_DEPTH):
            if current is None or current in own:
                break
            current = based_on.get(current)
        if current in own:
            resolved[style_id] = own[current]
    return resolved, default


def _list_instances(
    numbering: Element, style_lists: dict[str, tuple[str | None, int | None]]
) -> tuple[dict[str, _ListInstance], dict[str, dict[int, _ListLevel]]]:
    """Each `w:num` and the abstract definition it counts with.

    An abstract definition that only names a numbering style
    (`w:numStyleLink`) counts with the definition that style's list uses, as
    mammoth reads its levels from it.
    """
    abstracts = {
        abstract.get(_q("w:abstractNumId")) or "": abstract
        for abstract in numbering.findall(_q("w:abstractNum"))
    }
    # By the identifiers as written, as mammoth matches them.
    referenced = {
        num.get(_q("w:numId")) or "": (
            _value(num.find(_q("w:abstractNumId"))),
            num,
        )
        for num in numbering.findall(_q("w:num"))
    }

    def definition(abstract_id: str) -> str | None:
        for _ in range(_STYLE_DEPTH):
            abstract = abstracts.get(abstract_id)
            if abstract is None:
                return None
            link = abstract.find(_q("w:numStyleLink"))
            if link is None:
                return abstract_id
            num_id, _ = style_lists.get(link.get(_q("w:val")) or "", (None, None))
            linked = referenced.get(num_id or "")
            if linked is None or linked[0] is None:
                return None
            abstract_id = linked[0]
        return None

    instances: dict[str, _ListInstance] = {}
    for num_id, (abstract_number, num) in referenced.items():
        resolved = None if abstract_number is None else definition(abstract_number)
        if resolved is None:
            continue
        overrides: dict[int, int | None] = {}
        for override in num.findall(_q("w:lvlOverride")):
            level = _number(override, "w:ilvl")
            start = override.find(_q("w:startOverride"))
            # An override's own `w:lvl` restates the level; Word counts from
            # the abstract definition's start all the same (docs/09 P31).
            if level is not None and start is not None:
                overrides[level] = _number(start)
        instances[num_id] = _ListInstance(abstract=resolved, overrides=overrides)
    levels = {
        abstract_id: _list_levels(abstracts[abstract_id])
        for abstract_id in {instance.abstract for instance in instances.values()}
    }
    return instances, levels


def _paragraph_list(
    paragraph: Element,
    style_lists: dict[str, tuple[str | None, int | None]],
    default_style: str | None,
) -> tuple[str, int] | None:
    """The `(numId, level)` a paragraph is numbered with, if any.

    Its own `w:numPr` first, then its style's; `w:numId` 0 removes the
    numbering a style would give.
    """
    properties = paragraph.find(_q("w:pPr"))
    own = None if properties is None else properties.find(_q("w:numPr"))
    style = None if properties is None else properties.find(_q("w:pStyle"))
    style_id = default_style if style is None else style.get(_q("w:val"))
    style_num, style_level = style_lists.get(style_id or "", (None, None))
    own_num = None if own is None else own.find(_q("w:numId"))
    num_id = style_num if own_num is None else own_num.get(_q("w:val"))
    if not num_id or num_id == "0":
        return None
    own_level = None if own is None else _number(own.find(_q("w:ilvl")))
    level = own_level if own_level is not None else style_level
    return num_id, 0 if level is None else level


def _read_paragraphs(root: Element) -> list[Element]:
    """The body's paragraphs as mammoth reads them, in document order.

    mammoth reads an `mc:AlternateContent`'s Fallback, and a paragraph whose
    mark is a tracked deletion as part of the paragraph after it, as Word's
    final view joins them.
    """
    paragraphs: list[Element] = []
    stack = [root]
    while stack:
        element = stack.pop()
        if element.tag == _q("mc:Choice"):
            continue
        if element.tag == _q("w:p"):
            mark = element.find(f"{_q('w:pPr')}/{_q('w:rPr')}/{_q('w:del')}")
            if mark is None:
                paragraphs.append(element)
        stack.extend(reversed(element))
    return paragraphs


class _ListCounter:
    """Word's counters, one set per abstract definition (docs/09 P31).

    Every `w:num` that uses a definition counts on the same set; a
    `w:startOverride` sets its level's count when that `w:num` first reaches
    the level, and is the level's start for that `w:num` from then on.  Using
    a level restarts every deeper level its `w:lvlRestart` allows, and gives
    each shallower level that has not been used its start, so the next item
    there is counted past it.
    """

    def __init__(
        self,
        instances: dict[str, _ListInstance],
        levels: dict[str, dict[int, _ListLevel]],
    ) -> None:
        self._instances = instances
        self._levels = levels
        self._counts: dict[str, dict[int, int | None]] = {}
        self._reached: set[tuple[str, int]] = set()

    def _start(self, instance: _ListInstance, level: int) -> int | None:
        if level in instance.overrides:
            return instance.overrides[level]
        definition = self._levels[instance.abstract].get(level)
        return 0 if definition is None else definition.start

    def count(self, num_id: str, level: int) -> tuple[_ListLevel | None, int | None]:
        """The level a paragraph is numbered at, and the number Word gives it.

        The level is None where Word knows no such list or level (mammoth
        then reads no list item), and the number None where it is unknown.
        """
        instance = self._instances.get(num_id)
        if instance is None:
            return None, None
        definitions = self._levels[instance.abstract]
        counts = self._counts.setdefault(instance.abstract, {})
        previous = counts.get(level)
        if level in instance.overrides and (num_id, level) not in self._reached:
            value = instance.overrides[level]
        elif level not in counts:
            value = self._start(instance, level)
        else:
            value = None if previous is None else previous + 1
        self._reached.add((num_id, level))
        counts[level] = value
        for shallower in range(level):
            if shallower not in counts:
                counts[shallower] = self._start(instance, shallower)
        for deeper in [index for index in counts if index > level]:
            definition = definitions.get(deeper)
            restart = None if definition is None else definition.restart
            if restart is None or (restart != 0 and level < restart):
                del counts[deeper]
        return definitions.get(level), value


def number_word_lists(data: bytes) -> tuple[bytes, WordListNumbers | None]:
    """A Word file whose numbered paragraphs carry the number Word shows.

    mammoth reads a list item's level and whether it is ordered, never its
    number, so every list it writes counts from 1.  Each paragraph Word
    numbers with an ordered format gets a text run of its mark, ahead of its
    own text, and the numbers go back in `WordListNumbers` for the step that
    writes the page from mammoth's HTML to set and strip.  A paragraph whose
    number cannot be worked out gets no mark, so that step finds an item it
    cannot number and says so.  None comes back, and the bytes as they came,
    when there is no paragraph to mark.
    """
    archive = _open(data)
    if archive is None:
        return data, None
    with archive:
        document = _main_part(archive)
        if document is None:
            return data, None
        related = {
            kind: target for _, kind, target in _relationships(archive, document)
        }
        numbering = (
            _read(archive, related["numbering"]) if "numbering" in related else None
        )
        if numbering is None:
            return data, None
        styles = _read(archive, related["styles"]) if "styles" in related else None
        root = _read(archive, document)
        if root is None:
            return data, None
    style_lists, default_style = _style_numbering(styles)
    instances, levels = _list_instances(numbering, style_lists)
    unreadable = {
        abstract_id
        for abstract_id, definitions in levels.items()
        if any(definition.start is None for definition in definitions.values())
    } | {
        instance.abstract
        for instance in instances.values()
        if None in instance.overrides.values()
    }
    counter = _ListCounter(instances, levels)
    numbers = WordListNumbers(
        mark=LIST_MARK_OPEN + hashlib.sha256(data).hexdigest()[:16], values=[]
    )
    for paragraph in _read_paragraphs(root):
        numbered = _paragraph_list(paragraph, style_lists, default_style)
        if numbered is None:
            continue
        level, value = counter.count(*numbered)
        if level is None or not level.ordered:
            continue
        if instances[numbered[0]].abstract in unreadable or value is None:
            continue
        # A count below 0 comes from a negative start or override, and Word
        # shows no number on one.
        shown = level.shown and value >= 0
        run = Element(_q("w:r"))
        SubElement(run, _q("w:t")).text = numbers.item_mark(len(numbers.values))
        properties = paragraph.find(_q("w:pPr"))
        paragraph.insert(
            0 if properties is None else list(paragraph).index(properties) + 1, run
        )
        numbers.values.append(value if shown else None)
    if not numbers.values:
        return data, None
    return _rebuild(data, {document: _serialize(root)}), numbers
