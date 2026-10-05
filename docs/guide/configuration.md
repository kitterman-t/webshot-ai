# Configuration

Settings can come from three places. They are resolved in one order, and it does
not vary:

> **flags > `WEBSHOT_*` environment variables > `--config` file > defaults**

A flag wins even when it is set to the value that is already the default:
`--scale 0.9` is a request, not an absence, and a config file must not silently
win an argument you thought you had made.

## The file is never discovered

WebShot reads a configuration file **only from a path someone named**:

```bash
webshot https://example.com --config ./webshot.toml
WEBSHOT_CONFIG=/etc/webshot.toml webshot https://example.com
```

`--config` outranks `WEBSHOT_CONFIG`. There is deliberately **no
`./webshot.toml` discovery**, and adding it would be a security regression, not
a convenience. WebShot is routinely pointed at a directory of material it did
not produce; a tool that reads settings from wherever it happens to be started
can be handed a `user_agent`, an injected stylesheet, or a set of MCP filesystem
roots by that directory. The refusal has its own test.

A missing file, malformed TOML, an unknown key, or a value of the wrong type is
a usage error — exit `2`, with the file and the key named.

## The file

```toml
# webshot.toml

[capture]
mode = "clean"                 # or "faithful"
auto_selector = true
exclude = [".newsletter", "aside.promo"]
delay = 1.0                    # seconds after load
timeout = 120.0                # navigation timeout, seconds
scroll = true                  # the positive sense of --no-scroll
max_scrolls = 100
scroll_delay = 0.25
user_agent = "…"
allow_http_errors = false
require_content = false        # --require-content: an empty capture becomes exit 5
max_assets = 50
css = "./print-tweaks.css"
ai_bundle = true               # the positive sense of --no-ai-bundle
legacy_bundle = false

[pdf]
format = "Letter"              # Letter | Legal | Tabloid | A3 | A4 | A5
landscape = false
margin = "0.6in"
scale = 0.9
media = "print"                # or "screen"
prefer_css_page_size = false
header_footer = true           # positive sense of --no-header-footer
tagged = true                  # positive sense of --no-tagged-pdf
outline = true                 # positive sense of --no-outline
pdfa = false                   # protected-viewer captures only
validate_pdf = "report"        # or "strict"

[ocr]
enabled = true                 # positive sense of --no-ocr
require = false                # --require-ocr: missing OCR becomes exit 6
language = "eng"
psm = 11                       # Tesseract page-segmentation mode
engine = "tesseract"           # or "rapid"

[mcp]
# See the MCP guide — these are the agent-facing boundary.
roots = ["~/captures"]
output_root = "~/captures"
allow_private_networks = false
read_asset_max_bytes = 5_242_880
markdown_page_bytes = 262_144
max_chunks = 200

[mcp.auth_profiles]
work = "~/.webshot/profiles/work"
```

Every negated flag is configured in its **positive** sense. `no_scroll = false`
is a double negative nobody reads correctly, so the setting is `scroll`.

Settings that are deliberately *not* configurable from a file: the source, the
output path, `--storage-state`, `--auth-profile`, `--interactive-auth`, and
`--protected-viewer`. Those are per-run decisions, and two of them carry
credentials.

## The environment

Every setting above has an environment variable, mechanically named
`WEBSHOT_<TABLE>_<KEY>`:

```bash
export WEBSHOT_CAPTURE_MODE=faithful
export WEBSHOT_PDF_FORMAT=A4
export WEBSHOT_OCR_ENGINE=rapid
export WEBSHOT_MCP_ALLOW_PRIVATE_NETWORKS=true
```

Values are strings and are validated exactly as the file's are, so
`WEBSHOT_PDF_SCALE=abc` fails the same way `scale = "abc"` does, naming the
variable.

**List-valued settings are file-or-flag only, with one exception.**
`WEBSHOT_MCP_ROOTS` accepts a list because roots are paths, and it splits on the
platform's path separator (`:` on macOS and Linux):

```bash
export WEBSHOT_MCP_ROOTS=/srv/captures:/srv/shared
```

`capture.exclude` has no environment variable on purpose: CSS selectors contain
both commas and colons (`a:hover`, `h1, h2`), so any separator would be
ambiguous.

## Checking what resolved

The QA report records the options **as resolved**, after all three layers:

```bash
webshot https://example.com --config ./webshot.toml --report report.json
jq .options report.json
```

Credential-bearing options are not in it. `--storage-state` and
`--auth-profile` reduce to `auth_mode`, because a QA report is a file people
attach to tickets and the location of a session store is not something it needs
to carry.

For the MCP server, `webshot mcp --config … --print-roots` prints the resolved
boundary and exits without serving.
