"""The pull request behind a merge queue entry, for the checks that read it.

On the `merge_group` event, GitHub gives the workflow a temporary branch of
the merge queue, `refs/heads/gh-readonly-queue/<base>/pr-<number>-<sha>`, and
no pull request. The `Signed-off-by`, `Conventional Commits title`,
`Documentation updated` and `Impact on other repositories` checks verify a property of
the pull request (its commits, its title, its diff and labels, its
description), so on the queue they check the same
pull request again, read from the API at that moment (docs/adr/0040). Each
queue entry has its own branch and its own checks, so every pull request of a
group is checked on its own entry.

This script prints the pull request as JSON, with only the fields the checks
use. It fails, and the check with it, when the reference is not a merge queue
branch of the expected base, or when the pull request is not open against it.

Usage: MERGE_GROUP_HEAD_REF=<ref> GH_TOKEN=<token> GITHUB_REPOSITORY=<owner/repo> \\
       python scripts/merge_queue.py [--base main]
"""

import argparse
import json
import os
import re
import sys
import urllib.request
from collections.abc import Callable
from typing import Any

_HEAD_REF = re.compile(
    r"^refs/heads/gh-readonly-queue/(?P<base>.+)/pr-(?P<number>[1-9][0-9]*)-[0-9a-f]{40}"
)

Fetch = Callable[[str], dict[str, Any]]


class QueueError(Exception):
    """The merge queue entry cannot be tied to an open pull request."""


def pull_request_number(head_ref: str, base: str) -> int:
    """Number of the pull request of a merge queue branch of `base`."""
    match = _HEAD_REF.fullmatch(head_ref)
    if match is None:
        raise QueueError(f"{head_ref!r} is not a merge queue branch")
    if match["base"] != base:
        raise QueueError(f"{head_ref!r} is in the merge queue of {match['base']!r}, not {base!r}")
    return int(match["number"])


def summary(pull: dict[str, Any], base: str) -> dict[str, Any]:
    """The fields the checks need, after checking the pull request is queued for `base`."""
    if pull.get("state") != "open":
        raise QueueError(f"pull request #{pull.get('number')} is not open")
    if pull["base"]["ref"] != base:
        target = pull["base"]["ref"]
        raise QueueError(f"pull request #{pull['number']} targets {target!r}, not {base!r}")
    return {
        "number": pull["number"],
        "title": pull["title"],
        "body": pull.get("body") or "",
        "author": pull["user"]["login"],
        "base_sha": pull["base"]["sha"],
        "head_sha": pull["head"]["sha"],
        "labels": [label["name"] for label in pull.get("labels", [])],
    }


def fetch_from_api(url: str) -> dict[str, Any]:
    """GET `url` from the GitHub API with the workflow token."""
    request = urllib.request.Request(  # noqa: S310 - the URL is built from GITHUB_API_URL
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {os.environ['GH_TOKEN']}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        data: dict[str, Any] = json.load(response)
    return data


def queued_pull_request(
    head_ref: str, base: str, repository: str, api: str, fetch: Fetch
) -> dict[str, Any]:
    """The queued pull request of `head_ref`, checked and summarized."""
    number = pull_request_number(head_ref, base)
    return summary(fetch(f"{api}/repos/{repository}/pulls/{number}"), base)


def main(argv: list[str] | None = None, fetch: Fetch = fetch_from_api) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="main", help="base branch of the merge queue")
    args = parser.parse_args(argv)
    try:
        pull = queued_pull_request(
            os.environ.get("MERGE_GROUP_HEAD_REF", ""),
            args.base,
            os.environ["GITHUB_REPOSITORY"],
            os.environ.get("GITHUB_API_URL", "https://api.github.com"),
            fetch,
        )
    except QueueError as error:
        sys.stderr.write(f"Merge queue entry: {error}.\n")
        return 1
    sys.stdout.write(json.dumps(pull) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
