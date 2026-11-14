"""Fail a pull request whose description does not say what it means for Home Assistant.

The Home Assistant app (HouseCairn/home-assistant-addons) is a layer over the image
of this repository, and the Home Assistant integration (HouseCairn/home-assistant-
housecairn) a client of its API (docs/adr/0075). A change here may need a change
there: an option or a variable of the app, a behaviour behind Home Assistant's
Ingress, an API the integration calls. The pull request template has a section
"Home Assistant impact" with three boxes; this check fails when:

- the section is missing, or no box is ticked;
- "None" is ticked together with another box;
- "The app must change" is ticked without a link to an issue or a pull request of
  HouseCairn/home-assistant-addons in the section, or "The integration must change"
  without one of HouseCairn/home-assistant-housecairn.

A link is `https://github.com/HouseCairn/<repository>/issues/<n>` or `/pull/<n>`, or
`HouseCairn/<repository>#<n>`. Pull requests opened by Dependabot (`PR_AUTHOR`) are
exempt: they use no template.

Usage: PR_BODY=<description> PR_AUTHOR=<login> python scripts/check_ha_impact.py
"""

import os
import re
import sys

HEADING = "Home Assistant impact"
EXEMPT_AUTHOR = "dependabot[bot]"
APP_REPOSITORY = "HouseCairn/home-assistant-addons"
INTEGRATION_REPOSITORY = "HouseCairn/home-assistant-housecairn"
# The labels of the three boxes, as the template writes them; matched at the start
# of the box's text, without regard to case.
NONE = "none"
APP = "the app must change"
INTEGRATION = "the integration must change"

_SECTION = re.compile(
    rf"^##[ \t]+{re.escape(HEADING)}[ \t]*$(?P<body>.*?)(?=^##?[ \t]|^---[ \t]*$|\Z)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_BOX = re.compile(r"^[ \t]*[-*][ \t]+\[(?P<mark>[ xX])\][ \t]+(?P<text>.+)$", re.MULTILINE)


def link(repository: str) -> re.Pattern[str]:
    name = re.escape(repository)
    return re.compile(
        rf"(?:https://github\.com/{name}/(?:issues|pull)/\d+\b|(?<![\w/.-]){name}#\d+\b)",
        re.IGNORECASE,
    )


def section(body: str) -> str | None:
    """The text of the section, without HTML comments, or None when it is missing."""
    match = _SECTION.search(_COMMENT.sub("", body.replace("\r\n", "\n")))
    return match["body"] if match else None


def ticked(text: str) -> list[str]:
    """The texts of the ticked boxes, in lower case."""
    return [m["text"].strip().lower() for m in _BOX.finditer(text) if m["mark"] in "xX"]


def problems(body: str, author: str = "") -> list[str]:
    """Why the description fails the rule, or an empty list."""
    if author == EXEMPT_AUTHOR:
        return []
    text = section(body or "")
    if text is None:
        return [
            f'The description has no "## {HEADING}" section. Copy it from the pull request '
            "template (.github/pull_request_template.md) and tick one box."
        ]
    boxes = ticked(text)
    none = any(box.startswith(NONE) for box in boxes)
    app = any(box.startswith(APP) for box in boxes)
    integration = any(box.startswith(INTEGRATION) for box in boxes)
    if not (none or app or integration):
        return [
            f'Tick one box of "{HEADING}": None, The app must change, or The integration '
            "must change."
        ]
    found = []
    if none and (app or integration):
        found.append(
            f'"{HEADING}": "None" is ticked together with a change; tick only what is true.'
        )
    if app and not link(APP_REPOSITORY).search(text):
        found.append(
            f'"{HEADING}": the app must change, but the section has no link to an issue or a '
            f"pull request of {APP_REPOSITORY} (https://github.com/{APP_REPOSITORY}/issues/<n>)."
        )
    if integration and not link(INTEGRATION_REPOSITORY).search(text):
        found.append(
            f'"{HEADING}": the integration must change, but the section has no link to an issue or '
            f"a pull request of {INTEGRATION_REPOSITORY} "
            f"(https://github.com/{INTEGRATION_REPOSITORY}/issues/<n>)."
        )
    return found


def main() -> int:
    body = os.environ.get("PR_BODY", "")
    author = os.environ.get("PR_AUTHOR", "")
    found = problems(body, author)
    if found:
        sys.stderr.writelines(f"{line}\n" for line in found)
        return 1
    if author == EXEMPT_AUTHOR:
        sys.stdout.write(f"Exempted: pull request of {EXEMPT_AUTHOR}.\n")
    else:
        text = section(body) or ""
        sys.stdout.write(f"{HEADING}: {', '.join(ticked(text))}.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
