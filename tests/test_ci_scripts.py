"""Scripts run by the CI workflows (scripts/, docs/adr/0014)."""

import http.server
import json
import os
import re
import subprocess
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

import pytest
import yaml

from scripts import (
    check_docs_updated,
    check_external_urls,
    check_ha_impact,
    check_pr_title,
    check_signoff,
    merge_queue,
)

# Home Assistant impact of a pull request (scripts/check_ha_impact.py, docs/adr/0075)

_TEMPLATE_PATH = Path(__file__).resolve().parents[1] / ".github" / "pull_request_template.md"
_TEMPLATE = _TEMPLATE_PATH.read_text(encoding="utf-8")
_APP_LINK = "https://github.com/HouseCairn/home-assistant-addons/issues/12"
_INTEGRATION_LINK = "HouseCairn/home-assistant-housecairn#7"


def _body(*ticks: str, extra: str = "") -> str:
    body = _TEMPLATE
    for label in ticks:
        body = body.replace(f"- [ ] {label}", f"- [x] {label}")
    return body.replace("## AI assistance", f"{extra}\n\n## AI assistance")


def test_the_template_untouched_fails() -> None:
    found = check_ha_impact.problems(_TEMPLATE)
    assert len(found) == 1
    assert "Tick one box" in found[0]


def test_a_description_without_the_section_fails() -> None:
    found = check_ha_impact.problems("## Summary\n\nFixes a typo.\n")
    assert len(found) == 1
    assert 'no "## Home Assistant impact" section' in found[0]
    assert check_ha_impact.problems("") != []


@pytest.mark.parametrize(
    "body",
    [
        _body("None"),
        _body("The app must change", extra=f"App: {_APP_LINK}"),
        _body("The app must change", extra="HouseCairn/home-assistant-addons#12"),
        _body("The integration must change", extra=_INTEGRATION_LINK),
        _body(
            "The app must change",
            "The integration must change",
            extra=f"{_APP_LINK}\nhttps://github.com/HouseCairn/home-assistant-housecairn/pull/3",
        ),
        "## Home Assistant impact\r\n\r\n- [X] None\r\n",
    ],
)
def test_a_ticked_section_with_its_links_passes(body: str) -> None:
    assert check_ha_impact.problems(body) == []


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        # A change without a link, or with a link to another repository.
        (_body("The app must change"), "no link to an issue or a pull request of HouseCairn/"),
        (
            _body(
                "The app must change", extra="https://github.com/HouseCairn/housecairn/issues/12"
            ),
            "HouseCairn/home-assistant-addons",
        ),
        (
            _body("The integration must change", extra=_APP_LINK),
            "HouseCairn/home-assistant-housecairn",
        ),
        (
            _body(
                "The app must change",
                extra="https://github.com/HouseCairn/home-assistant-addons-fork/issues/1",
            ),
            "HouseCairn/home-assistant-addons",
        ),
        (_body("None", "The app must change", extra=_APP_LINK), '"None" is ticked together'),
        # The example link of the template's comment does not count.
        (
            _TEMPLATE.replace("- [ ] The app must change", "- [x] The app must change"),
            "no link to an issue or a pull request of HouseCairn/home-assistant-addons",
        ),
        # A link outside the section does not count either.
        (
            _body("The app must change").replace("## Summary\n", f"## Summary\n\n{_APP_LINK}\n"),
            "no link to an issue or a pull request of HouseCairn/home-assistant-addons",
        ),
    ],
)
def test_a_change_needs_a_link_to_its_repository(body: str, expected: str) -> None:
    found = check_ha_impact.problems(body)
    assert any(expected in line for line in found), found


def test_dependabot_is_exempt_and_no_one_else() -> None:
    assert check_ha_impact.problems("Bumps django.", "dependabot[bot]") == []
    assert check_ha_impact.problems("Bumps django.", "dependabot") != []
    assert check_ha_impact.problems("Bumps django.", "github-actions[bot]") != []


def test_main_reads_the_environment(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PR_BODY", _body("None"))
    monkeypatch.setenv("PR_AUTHOR", "alice")
    assert check_ha_impact.main() == 0
    assert "Home Assistant impact: none." in capsys.readouterr().out
    monkeypatch.setenv("PR_BODY", "## Summary\n")
    assert check_ha_impact.main() == 1
    assert "Home Assistant impact" in capsys.readouterr().err


# Pull request titles


@pytest.mark.parametrize(
    "title",
    [
        "feat: add yearly recurrence",
        "fix(schedule): skip archived rooms",
        "ci: add CI, supply-chain checks and CLA workflow",
        "feat(api)!: drop the v0 endpoints",
        "chore(deps): bump the minor-and-patch group with 3 updates",
        "docs(adr/0014): list the required checks",
        "revert: feat: add yearly recurrence",
    ],
)
def test_conventional_titles_pass(title: str) -> None:
    assert check_pr_title.problems(title) == []
    assert check_pr_title.main([title]) == 0


@pytest.mark.parametrize(
    "title",
    [
        "",
        "Add yearly recurrence",
        "feat:add yearly recurrence",
        "feat:  ",
        "Feat: add yearly recurrence",
        "feature: add yearly recurrence",
        "feat(Schedule): add yearly recurrence",
        "feat(): add yearly recurrence",
        "feat (api): add yearly recurrence",
        " feat: add yearly recurrence",
        "feat: " + "x" * 100,
    ],
)
def test_other_titles_fail(title: str) -> None:
    assert check_pr_title.problems(title) != []
    assert check_pr_title.main([title]) == 1


def test_title_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PR_TITLE", "fix: handle empty schedules")
    assert check_pr_title.main([]) == 0
    monkeypatch.setenv("PR_TITLE", "handle empty schedules")
    assert check_pr_title.main([]) == 1


def test_unknown_type_is_named(capsys: pytest.CaptureFixture[str]) -> None:
    check_pr_title.main(["feature: add yearly recurrence"])
    assert "unknown type 'feature'" in capsys.readouterr().err


# Sign-off (DCO)

AUTHOR = "Ada Example <ada@example.com>"


def _git(repo: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Ada Example",
        "GIT_AUTHOR_EMAIL": "ada@example.com",
        "GIT_COMMITTER_NAME": "Ada Example",
        "GIT_COMMITTER_EMAIL": "ada@example.com",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    result = subprocess.run(  # noqa: S603 - fixed program, test-controlled arguments
        ["git", "-C", str(repo), *args],  # noqa: S607 - git from PATH
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _commit(repo: Path, message: str) -> str:
    _git(repo, "commit", "--allow-empty", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _commit(tmp_path, "chore: initial commit")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_signed_off_commits_pass(repo: Path) -> None:
    base = _git(repo, "rev-parse", "HEAD")
    _commit(repo, f"feat: one\n\nSigned-off-by: {AUTHOR}")
    head = _commit(repo, "fix: two\n\nBody.\n\nSigned-off-by: Ada Example <ADA@example.com>")
    assert check_signoff.main([base, head]) == 0


def test_commit_without_signoff_fails(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    base = _git(repo, "rev-parse", "HEAD")
    _commit(repo, f"feat: one\n\nSigned-off-by: {AUTHOR}")
    unsigned = _commit(repo, "fix: two")
    assert check_signoff.main([base, unsigned]) == 1
    err = capsys.readouterr().err
    assert unsigned[:12] in err
    assert "fix: two" in err


def test_signoff_from_someone_else_fails(repo: Path) -> None:
    base = _git(repo, "rev-parse", "HEAD")
    head = _commit(repo, "feat: one\n\nSigned-off-by: Bob Example <bob@example.com>")
    assert check_signoff.main([base, head]) == 1


def test_signoff_must_be_a_trailer_line(repo: Path) -> None:
    base = _git(repo, "rev-parse", "HEAD")
    head = _commit(repo, f"feat: one\n\nNot a trailer: Signed-off-by: {AUTHOR}")
    assert check_signoff.main([base, head]) == 1


def test_merge_commits_are_skipped(repo: Path) -> None:
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "topic")
    _commit(repo, f"feat: one\n\nSigned-off-by: {AUTHOR}")
    _git(repo, "checkout", "-q", "main")
    _commit(repo, f"fix: on main\n\nSigned-off-by: {AUTHOR}")
    _git(repo, "checkout", "-q", "topic")
    _git(repo, "merge", "-q", "--no-ff", "--no-edit", "main")
    head = _git(repo, "rev-parse", "HEAD")
    commits = check_signoff.read_commits(base, head)
    assert len(commits) == 3
    assert check_signoff.unsigned(commits) == []
    assert check_signoff.main([base, head]) == 0


def test_empty_range_passes(repo: Path) -> None:
    head = _git(repo, "rev-parse", "HEAD")
    assert check_signoff.main([head, head]) == 0


def test_signoff_usage_error() -> None:
    assert check_signoff.main([]) == 2


DEPENDABOT_EMAIL = "49699333+dependabot[bot]@users.noreply.github.com"
# As Dependabot writes it: the sign-off address is not the author address.
DEPENDABOT_MESSAGE = (
    "chore(deps): bump django from 5.2.7 to 5.2.8\n\n"
    "Signed-off-by: dependabot[bot] <support@github.com>"
)


def _dependabot_commit(repo: Path) -> str:
    subprocess.run(  # noqa: S603 - fixed program, test-controlled arguments
        ["git", "-C", str(repo), "commit", "--allow-empty", "-q", "-m", DEPENDABOT_MESSAGE],  # noqa: S607
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "dependabot[bot]",
            "GIT_AUTHOR_EMAIL": DEPENDABOT_EMAIL,
            "GIT_COMMITTER_NAME": "GitHub",
            "GIT_COMMITTER_EMAIL": "noreply@github.com",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
        },
        capture_output=True,
        text=True,
        check=True,
    )
    return _git(repo, "rev-parse", "HEAD")


def test_dependabot_commits_pass_in_a_dependabot_pull_request(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = _git(repo, "rev-parse", "HEAD")
    head = _dependabot_commit(repo)
    monkeypatch.setenv("PR_AUTHOR", "dependabot[bot]")
    assert check_signoff.main([base, head]) == 0


def test_bot_address_in_someone_elses_pull_request_is_checked(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = _git(repo, "rev-parse", "HEAD")
    head = _dependabot_commit(repo)
    monkeypatch.setenv("PR_AUTHOR", "mallory")
    assert check_signoff.main([base, head]) == 1
    monkeypatch.delenv("PR_AUTHOR")
    assert check_signoff.main([base, head]) == 1


def test_only_listed_bots_are_exempt() -> None:
    commit = check_signoff.Commit(
        "a" * 40, ("b" * 40,), "123+other[bot]@users.noreply.github.com", "chore: x"
    )
    assert check_signoff.unsigned([commit], "other[bot]") == [commit]
    actions = check_signoff.Commit(
        "c" * 40, ("b" * 40,), "41898282+github-actions[bot]@users.noreply.github.com", "chore: y"
    )
    assert check_signoff.unsigned([actions], "github-actions[bot]") == []
    assert check_signoff.unsigned([actions], "dependabot[bot]") == [actions]


# External URLs


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_external_urls_are_reported(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(tmp_path, "templates/base.html", '<link href="/static/app.css">\n')
    _write(tmp_path, "templates/page.html", '\n<script src="https://cdn.example.com/x.js">\n')
    _write(tmp_path, "static/css/app.css", "@import url(//fonts.example.net/css);\n")
    found = check_external_urls.offenders(tmp_path)
    assert [(p.name, n, url) for p, n, url in found] == [
        ("app.css", 1, "//fonts.example.net"),
        ("page.html", 2, "https://cdn.example.com"),
    ]
    assert check_external_urls.main([str(tmp_path)]) == 1
    assert "page.html:2" in capsys.readouterr().err


def test_local_and_non_web_files_pass(tmp_path: Path) -> None:
    _write(tmp_path, "templates/base.html", '<a href="/homes/">Homes</a>\n')
    _write(tmp_path, "static/vendor/LICENSE", "See https://example.com/license\n")
    _write(tmp_path, "static/vendor/README.md", "From https://example.com/\n")
    assert check_external_urls.offenders(tmp_path) == []
    assert check_external_urls.main([str(tmp_path)]) == 0


def test_repository_has_no_external_url() -> None:
    assert check_external_urls.main([]) == 0


# Merge queue entries (scripts/merge_queue.py, docs/adr/0040)

_QUEUE_SHA = "929f8209d40f77f4abc622a499c93a83babdbe64"


def _pull(**changes: object) -> dict[str, object]:
    pull: dict[str, object] = {
        "number": 104,
        "state": "open",
        "title": "feat: add yearly recurrence",
        "user": {"login": "alice"},
        "base": {"ref": "main", "sha": "b" * 40},
        "head": {"sha": "h" * 40},
        "labels": [{"name": "no-docs-needed"}],
    }
    pull.update(changes)
    return pull


def test_queue_branch_gives_the_pull_request_number() -> None:
    ref = f"refs/heads/gh-readonly-queue/main/pr-104-{_QUEUE_SHA}"
    assert merge_queue.pull_request_number(ref, "main") == 104


@pytest.mark.parametrize(
    "ref",
    [
        "",
        "refs/heads/main",
        f"refs/heads/feature/pr-104-{_QUEUE_SHA}",
        f"refs/heads/gh-readonly-queue/release/pr-104-{_QUEUE_SHA}",
        f"refs/heads/gh-readonly-queue/main/pr-0104-{_QUEUE_SHA}",
        "refs/heads/gh-readonly-queue/main/pr-104-abc",
        f"refs/heads/gh-readonly-queue/main/pr-104-{_QUEUE_SHA}\n",
    ],
)
def test_other_references_are_refused(ref: str) -> None:
    with pytest.raises(merge_queue.QueueError):
        merge_queue.pull_request_number(ref, "main")


def test_queued_pull_request_is_read_from_the_api() -> None:
    urls: list[str] = []

    def fetch(url: str) -> dict[str, object]:
        urls.append(url)
        return _pull()

    ref = f"refs/heads/gh-readonly-queue/main/pr-104-{_QUEUE_SHA}"
    pull = merge_queue.queued_pull_request(ref, "main", "o/r", "https://api.example.com", fetch)
    assert urls == ["https://api.example.com/repos/o/r/pulls/104"]
    assert pull == {
        "number": 104,
        "title": "feat: add yearly recurrence",
        "body": "",
        "author": "alice",
        "base_sha": "b" * 40,
        "head_sha": "h" * 40,
        "labels": ["no-docs-needed"],
    }


@pytest.mark.parametrize(
    "pull",
    [_pull(state="closed"), _pull(base={"ref": "release", "sha": "b" * 40})],
)
def test_a_pull_request_not_open_against_the_base_is_refused(pull: dict[str, object]) -> None:
    with pytest.raises(merge_queue.QueueError):
        merge_queue.summary(pull, "main")


def test_main_prints_the_pull_request_or_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    ref = f"refs/heads/gh-readonly-queue/main/pr-104-{_QUEUE_SHA}"
    monkeypatch.setenv("MERGE_GROUP_HEAD_REF", ref)
    assert merge_queue.main([], fetch=lambda url: _pull()) == 0
    assert json.loads(capsys.readouterr().out)["number"] == 104
    assert merge_queue.main([], fetch=lambda url: _pull(state="closed")) == 1
    assert "not open" in capsys.readouterr().err
    monkeypatch.setenv("MERGE_GROUP_HEAD_REF", "refs/heads/main")
    assert merge_queue.main([], fetch=lambda url: _pull()) == 1


# Every workflow of a required check also runs on the merge queue: a required
# check that never reports on a queue entry blocks the queue (docs/adr/0040).
_REQUIRED_CHECK_WORKFLOWS = (
    "ci.yml",
    "cla.yml",
    "dco.yml",
    "dependency-review.yml",
    "docs-updated.yml",
    "docs.yml",
    "ha-impact.yml",
    "image.yml",
    "pr-title.yml",
    "zizmor.yml",
)


@pytest.mark.parametrize("name", _REQUIRED_CHECK_WORKFLOWS)
def test_required_check_workflows_run_on_the_merge_queue(name: str) -> None:
    workflow = Path(__file__).parent.parent / ".github" / "workflows" / name
    text = workflow.read_text(encoding="utf-8")
    on_block = text.split("\non:\n", 1)[1].split("\n\n", 1)[0]
    assert "\n  merge_group:\n    types: [checks_requested]" in f"\n{on_block}"


# Retries of the downloads made by the CI (.github/scripts/retry.sh)

RETRY = Path(__file__).resolve().parents[1] / ".github" / "scripts" / "retry.sh"
# Fails until it has been run `succeed_at` times, counting its runs in a file.
FLAKY = 'n=$(($(cat "$1" 2>/dev/null || echo 0) + 1)); echo "$n" >"$1"; [ "$n" -ge "$2" ] || exit 7'


def _retry(
    tmp_path: Path, succeed_at: int, attempts: int
) -> tuple[subprocess.CompletedProcess[str], int]:
    counter = tmp_path / "runs"
    result = subprocess.run(  # noqa: S603 - fixed program, test-controlled arguments
        ["bash", str(RETRY), "bash", "-c", FLAKY, "flaky", str(counter), str(succeed_at)],  # noqa: S607
        env={**os.environ, "RETRY_ATTEMPTS": str(attempts), "RETRY_DELAY": "0"},
        capture_output=True,
        text=True,
        check=False,
    )
    return result, int(counter.read_text())


def test_retry_succeeds_after_transient_failures(tmp_path: Path) -> None:
    result, runs = _retry(tmp_path, succeed_at=3, attempts=4)
    assert (result.returncode, runs) == (0, 3)
    assert "attempt 2 of 4" in result.stderr


def test_retry_keeps_the_last_failure(tmp_path: Path) -> None:
    result, runs = _retry(tmp_path, succeed_at=10, attempts=4)
    assert (result.returncode, runs) == (7, 4)
    assert "failed 4 times, last exit status 7" in result.stderr


def test_retry_runs_a_successful_command_once(tmp_path: Path) -> None:
    result, runs = _retry(tmp_path, succeed_at=1, attempts=4)
    assert (result.returncode, runs, result.stderr) == (0, 1, "")


def test_retry_needs_a_command() -> None:
    result = subprocess.run(  # noqa: S603 - fixed program
        ["bash", str(RETRY)],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "usage" in result.stderr


# Tools taken from GitHub releases (.github/scripts/release-asset.sh)

SCRIPTS = Path(__file__).resolve().parents[1] / ".github" / "scripts"
RELEASE_ASSET = SCRIPTS / "release-asset.sh"
IMAGE_WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "image.yml"
_ASSET = b"archive content\n"


class _Releases(http.server.BaseHTTPRequestHandler):
    """A release API: tag v1 of o/r has asset 7, `tool.tar.gz`, served by a redirect."""

    failures_left = 0
    requests: ClassVar[list[tuple[str, str, str]]] = []

    def do_GET(self) -> None:
        type(self).requests.append(
            (self.path, self.headers.get("Accept", ""), self.headers.get("Authorization", ""))
        )
        if type(self).failures_left > 0:
            type(self).failures_left -= 1
            self._answer(504, b"")
        elif self.path == "/repos/o/r/releases/tags/v1":
            assets = [{"id": 6, "name": "tool.tar.gz.sig"}, {"id": 7, "name": "tool.tar.gz"}]
            self._answer(200, json.dumps({"tag_name": "v1", "assets": assets}).encode())
        elif self.path == "/repos/o/r/releases/assets/7":
            self.send_response(302)
            self.send_header("Location", "/storage/tool.tar.gz")
            self.end_headers()
        elif self.path == "/storage/tool.tar.gz":
            self._answer(200, _ASSET)
        else:
            self._answer(404, b"{}")

    def _answer(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - signature of the base class
        pass


@pytest.fixture
def releases() -> Iterator[str]:
    _Releases.failures_left = 0
    _Releases.requests = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Releases)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _release_asset(
    api: str,
    output: Path,
    *args: str,
    token: str | None = "test-token",  # noqa: S107 - test value
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "GITHUB_API_URL": api, "RELEASE_ASSET_RETRY_DELAY": "1"}
    env.pop("GITHUB_TOKEN", None)
    if token is not None:
        env["GITHUB_TOKEN"] = token
    return subprocess.run(  # noqa: S603 - fixed program, test-controlled arguments
        ["bash", str(RELEASE_ASSET), *(args or ("o/r", "v1", "tool.tar.gz")), str(output)],  # noqa: S607
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_release_asset_is_found_by_name_and_downloaded_through_the_api(
    releases: str, tmp_path: Path
) -> None:
    output = tmp_path / "tool.tar.gz"
    result = _release_asset(releases, output)
    assert result.returncode == 0, result.stderr
    assert output.read_bytes() == _ASSET
    assert [(path, accept) for path, accept, _ in _Releases.requests] == [
        ("/repos/o/r/releases/tags/v1", "application/vnd.github+json"),
        ("/repos/o/r/releases/assets/7", "application/octet-stream"),
        ("/storage/tool.tar.gz", "application/octet-stream"),
    ]
    assert _Releases.requests[0][2] == "Bearer test-token"
    assert f"from {releases}/repos/o/r/releases/assets/7" in result.stdout
    assert "test-token" not in result.stdout + result.stderr


def test_release_asset_retries_a_504(releases: str, tmp_path: Path) -> None:
    _Releases.failures_left = 2
    output = tmp_path / "tool.tar.gz"
    result = _release_asset(releases, output)
    assert result.returncode == 0, result.stderr
    assert output.read_bytes() == _ASSET
    assert len(_Releases.requests) == 5


@pytest.mark.parametrize(
    "args",
    [("o/r", "v1", "tool.tar"), ("o/r", "v2", "tool.tar.gz"), ("o/other", "v1", "tool.tar.gz")],
)
def test_release_asset_fails_without_that_asset(
    releases: str, tmp_path: Path, args: tuple[str, str, str]
) -> None:
    output = tmp_path / "tool.tar.gz"
    result = _release_asset(releases, output, *args)
    assert result.returncode != 0
    assert not output.exists()


def test_release_asset_needs_a_token_and_four_arguments(releases: str, tmp_path: Path) -> None:
    result = _release_asset(releases, tmp_path / "out", token=None)
    assert result.returncode != 0
    assert "GITHUB_TOKEN must be set" in result.stderr
    assert _Releases.requests == []
    usage = _release_asset(releases, tmp_path / "out", "o/r", "v1")
    assert usage.returncode == 2
    assert "usage" in usage.stderr


def test_release_asset_keeps_the_last_error_fatal() -> None:
    text = RELEASE_ASSET.read_text(encoding="utf-8")
    assert "set -euo pipefail" in text
    assert "-sSfL --retry 4" in text
    assert "--retry-all-errors --retry-max-time 120" in text
    assert '-H "Accept: application/octet-stream"' in text
    assert "|| true" not in text
    assert "releases/download" not in text.split("set -euo pipefail", 1)[1]


def _grype_step() -> str:
    text = IMAGE_WORKFLOW.read_text(encoding="utf-8")
    return text.split("      - name: Install Grype (checksum verified)\n", 1)[1].split(
        "\n      - ", 1
    )[0]


def test_grype_is_downloaded_through_the_api_then_checked() -> None:
    step = _grype_step()
    download = step.index(".github/scripts/release-asset.sh anchore/grype")
    check = step.index('echo "${GRYPE_SHA256}  $archive" | sha256sum --check --strict')
    extract = step.index('tar -xzf "$archive"')
    assert download < check < extract
    assert "GITHUB_TOKEN: ${{ github.token }}" in step
    assert "github.com/anchore/grype/releases/download" not in step
    assert "|| true" not in step
    assert "continue-on-error" not in step


# Documentation updated (scripts/check_docs_updated.py): user-facing files that
# the first pattern let through without documentation


@pytest.mark.parametrize(
    "path",
    [
        # Prefixed route and view modules.
        "src/housecairn/calendar/api_urls.py",
        "src/housecairn/notifications/api_urls.py",
        "src/housecairn/tasks/contractor_views.py",
        # Forms, views and API split into packages.
        "src/housecairn/tasks/forms/task.py",
        "src/housecairn/tasks/forms/__init__.py",
        "src/housecairn/homes/views/members.py",
        "src/housecairn/tasks/api/occurrences.py",
        # What the browser loads, and the in-app help links.
        "src/housecairn/static/css/app.css",
        "src/housecairn/static/js/calendar.js",
        "src/housecairn/core/help.py",
    ],
)
def test_more_user_facing_files_need_docs(path: str) -> None:
    # Internal modules next to them still pass without documentation.
    internal = [
        "src/housecairn/tasks/services.py",
        "src/housecairn/tasks/migrations/0006_task_notes.py",
        "src/housecairn/core/translations.py",
        "tests/test_tasks_api.py",
    ]
    assert check_docs_updated.user_facing([path, *internal]) == [path]
    assert check_docs_updated.problems([path], []) != []
    assert check_docs_updated.problems([path, "docs/screens/home.md"], []) == []


# Workflow shell steps that cannot pass when they fail

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"


def _run_steps(name: str) -> list[dict[str, str]]:
    workflow = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    return [
        step for job in workflow["jobs"].values() for step in job.get("steps", []) if "run" in step
    ]


def _all_run_steps() -> list[dict[str, str]]:
    return [step for path in sorted(WORKFLOWS.glob("*.yml")) for step in _run_steps(path.name)]


def test_a_piped_command_fails_its_step() -> None:
    # GitHub's default shell is `bash -e {0}`, without pipefail: the status of
    # `cmd | tee` would be the status of `tee`. `shell: bash` adds pipefail;
    # a script may also read PIPESTATUS itself.
    unsafe = [
        step["name"]
        for step in _all_run_steps()
        if "| tee" in step["run"]
        and step.get("shell") != "bash"
        and "set -o pipefail" not in step["run"]
        and "set -euo pipefail" not in step["run"]
        and "PIPESTATUS[0]" not in step["run"]
    ]
    assert unsafe == []


def test_image_tags_step_fails_with_its_script() -> None:
    step = next(s for s in _run_steps("image.yml") if s["name"] == "Tags for this ref")
    assert step["shell"] == "bash"


def test_a_wait_loop_ends_with_a_check_that_fails() -> None:
    # `for ...; do probe && break; sleep 1; done` ends with status 0 whether
    # the probe succeeded or not: the next line must probe once more and fail.
    loops = 0
    for step in _all_run_steps():
        lines = step["run"].splitlines()
        for i, line in enumerate(lines):
            loop = re.match(r"\s*for _ in \$\(seq .*?\); do (.+?) && break; sleep \d+; done$", line)
            if not loop:
                continue
            loops += 1
            after = lines[i + 1] if i + 1 < len(lines) else ""
            assert after.strip().startswith(loop.group(1)), step["name"]
            assert re.search(r"\|\| \{ cat \"[^\"]+\"; exit 1; \}$", after), step["name"]
    assert loops >= 2


# CI runs: tests in parallel, no third run on `main`


def test_ci_does_not_run_again_on_main() -> None:
    workflow = yaml.safe_load((WORKFLOWS / "ci.yml").read_text(encoding="utf-8"))
    # PyYAML reads the `on:` key as the boolean True.
    triggers = workflow[True]
    assert set(triggers) == {"pull_request", "merge_group"}
    assert triggers["merge_group"] == {"types": ["checks_requested"]}


@pytest.mark.parametrize(
    ("job", "step"),
    [
        ("tests-sqlite", "Tests with coverage (SQLite)"),
        ("tests-postgresql", "Tests with coverage (PostgreSQL)"),
    ],
)
def test_ci_tests_run_in_parallel_with_the_coverage_threshold(job: str, step: str) -> None:
    workflow = yaml.safe_load((WORKFLOWS / "ci.yml").read_text(encoding="utf-8"))
    run = next(s["run"] for s in workflow["jobs"][job]["steps"] if s.get("name") == step)
    assert " -n auto " in run
    assert "--cov=housecairn" in run
    assert "--cov-fail-under=85" in run


def test_gettext_lists_are_refreshed_only_when_the_install_fails() -> None:
    action = WORKFLOWS.parent / "actions" / "compile-messages" / "action.yml"
    steps = yaml.safe_load(action.read_text(encoding="utf-8"))["runs"]["steps"]
    script = next(s["run"] for s in steps if s["name"] == "Install GNU gettext")
    assert "if ! command -v msgfmt" in script
    first_install = script.index('"${apt[@]}" "${install[@]}"')
    assert script.index("update -q") > first_install
    assert "msgfmt --version" in script
