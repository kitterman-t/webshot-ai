# Fixture fonts

`ligature-fixture.ttf` is a subset of Noto Sans Regular 2.007 (SIL Open Font
License 1.1, [OFL.txt](OFL.txt)), renamed "WebShot Ligature Fixture". It keeps
printable ASCII, the presentation forms U+FB00–U+FB04, and the `liga` feature
that maps "ff", "fi", "fl", "ffi" and "ffl" onto them.

It exists so that the ligature tests (docs/09 P16-2) draw their text in the same
font on every machine. A system `sans-serif` resolves to Helvetica on macOS and
to whatever fontconfig picks on Linux, and whether that font ligates is not
something a test should depend on. `tests/test_ligatures.py` also checks that
Chromium printing this font with no WebShot preparation does put ligatures
into the text layer, so the test cannot pass because the font failed to load.

Regenerate it from the copy OCRmyPDF ships:

```python
from fontTools import subset
from fontTools.ttLib import TTFont

opts = subset.Options()
opts.layout_features = ["liga", "tnum", "kern"]
opts.hinting = False
opts.name_IDs = [0, 1, 2, 3, 4, 5, 6, 13, 14]
opts.notdef_outline = True
font = TTFont(".venv/lib/python3.12/site-packages/ocrmypdf/data/NotoSans-Regular.ttf")
subsetter = subset.Subsetter(opts)
subsetter.populate(unicodes=[*range(0x20, 0x7F), *range(0xFB00, 0xFB05)])
subsetter.subset(font)
for record in font["name"].names:
    if record.nameID in (1, 4):
        record.string = "WebShot Ligature Fixture"
    elif record.nameID == 6:
        record.string = "WebShotLigatureFixture-Regular"
    elif record.nameID == 3:
        record.string = "WebShotLigatureFixture-Regular; subset of Noto Sans 2.007"
font.save("tests/fixtures/fonts/ligature-fixture.ttf")
```
