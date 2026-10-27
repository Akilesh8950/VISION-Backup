"""Assemble the single-file VISION demo (demo.html).

Steps:
  1. cd app/frontend && npx vite build --config vite.demo.config.js
  2. python3 scripts/build_demo.py

Inlines the demo build's one JS chunk and one CSS file into a single
self-contained HTML at the workspace root (demo.html).  The result
runs the full React UI in demo/gallery mode - demo conversations,
settings, extensions - with no server and no assets folder.  The
production build in app/web is untouched.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

DIST = Path("/tmp/vision-demo-dist")
OUT = Path(__file__).resolve().parents[1] / "demo.html"

GALLERY_BOOTSTRAP = (
    "<script>(function(){"
    "try{var u=new URL(location.href);u.searchParams.set(\"gallery\",\"1\");"
    "history.replaceState(null,\"\",u)}catch(e){}"
    # Chrome forbids history entries pointing at blob: URLs, so when the
    # demo runs inside a blob iframe on http(s), replaceState above is a
    # no-op.  Fall back to making ?gallery visible to the one place the
    # app reads it (URLSearchParams.has('gallery')).
    "try{if(!/[?&]gallery=1/.test(location.search)){"
    "var P=window.URLSearchParams;"
    "var W=function(i){var p=new P(i);var h=p.has.bind(p);"
    "p.has=function(k){return k===\"gallery\"||h(k)};return p};"
    "W.prototype=P.prototype;window.URLSearchParams=W}}catch(e){}"
    "})();</script>"
)


def main() -> int:
    index = DIST / "index.html"
    if not index.is_file():
        print(f"Demo build not found at {DIST}; run the vite demo config first.", file=sys.stderr)
        return 1
    html = index.read_text(encoding="utf-8")

    css_files = sorted((DIST / "assets").glob("*.css"))
    js_files = sorted((DIST / "assets").glob("*.js"))
    if len(css_files) != 1 or len(js_files) != 1:
        print(f"Expected exactly one JS and one CSS file, got js={js_files} css={css_files}", file=sys.stderr)
        return 1

    css = css_files[0].read_text(encoding="utf-8")
    js = js_files[0].read_text(encoding="utf-8")
    # A JS string containing "</script>" would terminate the inline
    # block early; "<\/script>" is equivalent inside JS strings and
    # invisible to the HTML parser.
    js = js.replace("</script", "<\\/script")

    html, n_css = re.subn(
        r'<link[^>]*rel="stylesheet"[^>]*>',
        lambda _m: "<style>\n" + css + "\n</style>",
        html,
    )
    html, n_js = re.subn(
        r'<script[^>]*type="module"[^>]*src="[^"]*"[^>]*></script>',
        lambda _m: '<script type="module">\n' + js + "\n</script>",
        html,
    )
    if n_css != 1 or n_js != 1:
        print(f"Inline replacement failed (css={n_css}, js={n_js})", file=sys.stderr)
        return 1

    # Seed demo/gallery state instantly instead of waiting out the
    # native-bridge probe; harmless when it cannot run (sandboxed
    # iframes) because browser mode falls back to the same demo data.
    html = html.replace("</title>", "</title>\n" + GALLERY_BOOTSTRAP, 1)
    html = html.replace("<title>", "<title>VISION — Live demo").replace(
        "VISION — Live demoVISION", "VISION — Live demo"
    ) if "<title>VISION" in html else html

    leftover = re.findall(r'(?:src|href)="\./assets/[^"]*"', html)
    if leftover:
        print(f"External asset references remain: {leftover}", file=sys.stderr)
        return 1

    OUT.write_text(html, encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KiB)")
    _refresh_embedded_copy(html.encode("utf-8"))
    return 0


def _refresh_embedded_copy(demo_bytes: bytes) -> None:
    """Keep screenshots.html's embedded demo in sync, when present."""

    import base64
    import textwrap

    page = OUT.with_name("screenshots.html")
    if not page.is_file():
        return
    text = page.read_text(encoding="utf-8")
    marker = '<script type="text/plain" id="demo-b64">'
    if marker not in text:
        return
    b64 = "\n".join(textwrap.wrap(base64.b64encode(demo_bytes).decode(), 120))
    updated, count = re.subn(
        r'(<script type="text/plain" id="demo-b64">).*?(</script>)',
        lambda m: m.group(1) + "\n" + b64 + "\n" + m.group(2),
        text,
        flags=re.DOTALL,
    )
    if count == 1:
        page.write_text(updated, encoding="utf-8")
        print(f"refreshed embedded demo in {page}")


if __name__ == "__main__":
    raise SystemExit(main())
