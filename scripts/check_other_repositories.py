"""Fail a pull request whose description does not say what it means for the other repositories.

Three repositories follow this one (docs/adr/0075): the Home Assistant app
(HouseCairn/home-assistant-addons), a layer over the image of this repository; the Home
Assistant integration (HouseCairn/home-assistant-housecairn), a client of its API; and the
mobile app (HouseCairn/housecairn-mobile), which carries rules of this one (recurrence,
seasons, task templates, design tokens, data schema). A change here may need a change
there. The pull request template has a section "Impact on other repositories" with four
boxes; this check fails when:

- the section is missing, or no box is ticked;
- "None" is ticked together with another box;
- a repository's box is ticked without a link, in the section, to an issue or a pull
  request of that repository.

A link is `https://github.com/HouseCairn/<repository>/issues/<n>` or `/pull/<n>`, or
`HouseCairn/<repository>#<n>`. Pull requests opened by Dependabot (`PR_AUTHOR`) are
exempt: they use no template.

Usage: PR_BODY=<description> PR_AUTHOR=<login> python scripts/check_other_repositories.py
"""

import os
import re
import sys

HEADING = "Impact on other repositories"
EXEMPT_AUTHOR = "dependabot[bot]"
# The labels of the boxes, as the template writes them; matched at the start of the
# box's text, without regard to case.
NONE = "none"
# (label, repository) of the boxes that announce a change in another repository.
TARGETS = (
    ("home assistant app must change", "HouseCairn/home-assistant-addons"),
    ("home assistant integration must change", "HouseCairn/home-assistant-housecairn"),
    ("mobile app must change", "HouseCairn/housecairn-mobile"),
)

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
    changed = [(label, repo) for label, repo in TARGETS if any(b.startswith(label) for b in boxes)]
    if not (none or changed):
        return [
            f'Tick one box of "{HEADING}": None, or the repository that must change '
            "(Home Assistant app, Home Assistant integration, mobile app)."
        ]
    found = []
    if none and changed:
        found.append(
            f'"{HEADING}": "None" is ticked together with a change; tick only what is true.'
        )
    for label, repository in changed:
        if not link(repository).search(text):
            found.append(
                f'"{HEADING}": the {label}, but the section has no link to an issue or a '
                f"pull request of {repository} (https://github.com/{repository}/issues/<n>)."
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
