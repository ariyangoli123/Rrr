"""Inline the app into single files.

    python3 web/build_single.py

Writes two builds beside the sources:

* ``dist/standalone.html`` -- a complete document; open it from anywhere, or
  send it to someone as one file.
* ``dist/artifact.html``   -- the same page without the document wrapper, for
  hosts that supply their own ``<head>`` and ``<body>``.

The multi-file version in ``web/`` stays the one that installs as a PWA: a
service worker and a manifest need real URLs.
"""

from __future__ import annotations

import re
from pathlib import Path

WEB = Path(__file__).parent
DIST = WEB / "dist"

FONTS = ('<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
         'family=Barlow+Semi+Condensed:wght@500;600&family=IBM+Plex+Mono:wght@500;600&'
         'family=IBM+Plex+Sans:wght@400;500&display=swap">')


def read(name: str) -> str:
    return (WEB / name).read_text()


def bundled_script() -> str:
    """sim.js and app.js merged into one module -- no imports left to resolve."""
    sim = re.sub(r"^export ", "", read("sim.js"), flags=re.MULTILINE)
    app = read("app.js")
    app = re.sub(r"^import \{[^}]*\} from '\./sim\.js';\n", "", app, flags=re.MULTILINE)
    return f"/* ---- sim.js ---- */\n{sim}\n/* ---- app.js ---- */\n{app}"


def body_markup() -> str:
    html = read("index.html")
    match = re.search(r"<main class=\"app\">.*?</main>", html, flags=re.DOTALL)
    if not match:
        raise SystemExit("could not find the <main> block in index.html")
    return match.group(0)


def main() -> None:
    DIST.mkdir(parents=True, exist_ok=True)
    style = read("styles.css")
    script = bundled_script()
    body = body_markup()
    title = "Sedimentation Bench"

    fragment = (
        f"<title>{title}</title>\n{FONTS}\n<style>\n{style}\n</style>\n"
        f"{body}\n<script type=\"module\">\n{script}\n</script>\n"
    )
    (DIST / "artifact.html").write_text(fragment)

    standalone = (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1, viewport-fit=cover\">\n"
        f"<title>{title}</title>\n"
        "<meta name=\"description\" content=\"Blood sedimentation (ESR) simulated live in "
        "tubes of different geometry.\">\n"
        "<meta name=\"theme-color\" content=\"#e9edec\" media=\"(prefers-color-scheme: light)\">\n"
        "<meta name=\"theme-color\" content=\"#0f1417\" media=\"(prefers-color-scheme: dark)\">\n"
        f"{FONTS}\n<style>\n{style}\n</style>\n</head>\n<body>\n{body}\n"
        f"<script type=\"module\">\n{script}\n</script>\n</body>\n</html>\n"
    )
    (DIST / "standalone.html").write_text(standalone)

    for path in sorted(DIST.glob("*.html")):
        print(f"wrote {path} ({path.stat().st_size / 1024:.0f} kB)")


if __name__ == "__main__":
    main()
