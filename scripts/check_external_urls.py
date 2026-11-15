"""Fail if a template or static file refers to another host (docs/adr/0011).

Every page must load its scripts, styles, fonts and images from the instance
itself: no CDN, no third-party font, no tracking pixel.

Usage: python scripts/check_external_urls.py [<package directory>]
"""

import re
import sys
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[1] / "src" / "housecairn"
WEB_SUFFIXES = frozenset({".html", ".css", ".js"})
# An absolute or protocol-relative URL with a host name, as in
# "https://cdn.example.com/" or "//fonts.example.com/": the host name ends at
# the first character that cannot be part of it (path, port, query, quote, end
# of line). The top-level domain is letters, or an IDNA `xn--` label.
EXTERNAL_URL = re.compile(
    r"(?:https?:)?//[a-z0-9.-]+\.(?:[a-z]{2,}|xn--[a-z0-9-]+)(?![a-z0-9.-])", re.IGNORECASE
)

# XML namespace names, which a script writes into the elements it builds (an SVG drawn by a
# vendored library): an identifier the browser never fetches, not an address.
NAMESPACE_NAMES = re.compile(
    r"http://www\.w3\.org/(?:2000/svg|1999/xhtml|1999/xlink|1998/Math/MathML|2000/xmlns/)"
)


def web_files(root: Path) -> list[Path]:
    """Templates and static files of the package, sorted."""
    candidates = [*root.glob("templates/**/*"), *root.glob("static/**/*")]
    return sorted(p for p in candidates if p.is_file() and p.suffix in WEB_SUFFIXES)


def offenders(root: Path) -> list[tuple[Path, int, str]]:
    """(file, line number, URL) for each external URL found under `root`."""
    found = []
    for path in web_files(root):
        lines = path.read_text(encoding="utf-8").splitlines()
        for number, line in enumerate(lines, start=1):
            found += [
                (path, number, m.group(0))
                for m in EXTERNAL_URL.finditer(line)
                if not NAMESPACE_NAMES.match(line, m.start())
            ]
    return found


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    root = Path(args[0]) if args else DEFAULT_ROOT
    found = offenders(root)
    if found:
        sys.stderr.write("External URLs in templates or static files (docs/adr/0011):\n")
        for path, number, url in found:
            sys.stderr.write(f"- {path}:{number}: {url}\n")
        return 1
    sys.stdout.write(f"No external URL in {len(web_files(root))} template and static files.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
