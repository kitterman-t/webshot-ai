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

Every part is parsed through defusedxml (spec §6.6). Nothing upstream is
imported here: the equation converter is MarkItDown's, so the bridge hands it in.
"""

from __future__ import annotations

import copy
import io
import posixpath
import zipfile
import zlib
from collections.abc import Callable
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
            entry = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            entry.compress_type = info.compress_type
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
