"""The guard of the reads of the homes: an example home cannot slip into a list (docs/adr/0084).

Every app of the package is parsed, not a list of apps, so that a new app is covered from its
first commit. A read of `Home.objects`, `Home.all_objects`, `Membership.objects` or
`Membership.all_objects` fails the test unless the function that makes it goes through
`homes_for`, `homes_for_request` or `real_homes`, or the site is in `AUDITED` with its reason
(the way `NOT_SYNCED` of `tests/test_sync_guard.py` says why a table is not synced). A new read of
the homes of an account therefore fails here until it filters or is audited. `include_example=True`
is allowed only where the record says: the web pages of the example home itself, and the guide.

The guard cannot see a way out that reads no `Home` or `Membership`, such as a new file store or a
new network call: that case rests on the named tests of `tests/test_example_home_never_leaves.py`,
one per way out, and on the review of the lot.
"""

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest
from django.apps import apps

import housecairn

PACKAGE = Path(housecairn.__file__).resolve().parent

FILTERS = frozenset({"homes_for", "homes_for_request", "real_homes"})
MODELS = frozenset({"Home", "Membership"})
MANAGERS = frozenset({"objects", "all_objects"})
# Creating a row reads no list: only a read of the rows is a site.
CREATIONS = frozenset({"create", "bulk_create"})
# Where `include_example=True` may be said, and where the mark may be set.
INCLUDE_EXAMPLE_IN = ("homes/scoping.py", "guide/")
SETS_THE_MARK_IN = ("homes/services.py",)
WRITERS = frozenset(
    {"Home", "create", "update", "update_or_create", "get_or_create", "_create", "bulk_create"}
)

# The three groups of the audit of ADR 0084, one entry per function that reads without filtering.
TAKES_ITS_HOME = "reads the rows of one home that its caller already resolved and filtered"
SEES_EVERY_HOME = "must see every home, the example one included, on purpose"
AUDITED: dict[tuple[str, str], str] = {
    # Read the members of one home already resolved.
    ("homes/views.py", "HomeActionView.membership"): TAKES_ITS_HOME,
    ("homes/views.py", "MemberRemoveView._is_me"): TAKES_ITS_HOME,
    ("homes/views.py", "MemberView.get_object"): TAKES_ITS_HOME,
    ("homes/services.py", "role_of"): TAKES_ITS_HOME + " (the role of a person in it)",
    ("homes/services.py", "members"): TAKES_ITS_HOME + " (its members)",
    ("homes/services.py", "_lock_memberships"): TAKES_ITS_HOME + " (a lock of its members)",
    ("homes/services.py", "leave_home"): TAKES_ITS_HOME + " (who leaves it)",
    ("homes/services.py", "accept_invitation"): TAKES_ITS_HOME + " (the home of an invitation)",
    ("homes/current.py", "for_request"): TAKES_ITS_HOME + " (the roles in the homes it lists)",
    ("sync/reading.py", "home_entry"): TAKES_ITS_HOME + " (its entry of the sync API)",
    ("sync/pushing.py", "_check_references"): TAKES_ITS_HOME + " (a person of a push)",
    ("sync/repairs.py", "Session.finish"): "reads a home of a push already resolved by the route",
    ("tasks/services.py", "_check_links"): TAKES_ITS_HOME + " (the assignee of a task)",
    ("audit/services.py", "home_log"): TAKES_ITS_HOME + " (the owner check on a given home)",
    ("calendar/services.py", "entries"): TAKES_ITS_HOME + " (the homes given to the calendar)",
    ("calendar/services.py", "people_of"): TAKES_ITS_HOME + " (the homes given to the calendar)",
    ("calendar/ics.py", "_fingerprint"): TAKES_ITS_HOME + " (the homes of a feed, filtered)",
    # No read at all: a placeholder queryset, set to the homes of the person later.
    ("homes/api.py", "MemberViewSet"): "`Membership.objects.none()`: the queryset reads no row",
    ("apitokens/forms.py", "TokenForm"): "`Home.objects.none()`, replaced by `homes_for` in init",
    ("apitokens/forms.py", "ConnectionHomesMixin"): "`Home.objects.none()`, replaced in init",
    # Must see every home on purpose.
    ("homes/quotas.py", "_check_demo_total"): SEES_EVERY_HOME + " (the disk really held)",
    ("homes/quotas.py", "by_home"): SEES_EVERY_HOME + " (the administrator's numbers)",
    ("homes/quotas.py", "room_for"): SEES_EVERY_HOME + " (a touch of the row of the home)",
    ("homes/restore.py", "_check_free"): SEES_EVERY_HOME + " (an identifier already taken)",
    ("homes/restore.py", "_check_may_copy"): SEES_EVERY_HOME + " (the home an archive came from)",
    ("homes/restore.py", "restore_home"): SEES_EVERY_HOME + " (dates of the home it makes)",
    ("homes/services.py", "erase_home"): SEES_EVERY_HOME + " (erasing a home)",
    ("homes/services.py", "lift_all_unavailable_requirements"): SEES_EVERY_HOME
    + " (a rule that points at a provider that is gone)",
    ("guide/services.py", "remove_example"): SEES_EVERY_HOME + " (it deletes the example home)",
    ("accounts/services.py", "delete_account"): SEES_EVERY_HOME
    + " (the memberships of the account: the example home goes with it)",
    ("sync/changes.py", "record_change"): SEES_EVERY_HOME + " (a lock, and the mark itself)",
    ("sync/files.py", "receive"): SEES_EVERY_HOME + " (a row lock, nothing read out)",
    ("instance/providers.py", "_clean_slug"): SEES_EVERY_HOME + " (a provider in use by any home)",
    ("instance/providers.py", "_counts"): SEES_EVERY_HOME + " (a provider in use by any home)",
    ("instance/views.py", "OverviewView.get_context_data"): SEES_EVERY_HOME
    + " (a provider in use by any home)",
    ("demo/guards.py", "is_demo_home"): SEES_EVERY_HOME + " (they make homes)",
    ("homes/management/commands/load_demo_data.py", "Command.handle"): SEES_EVERY_HOME
    + " (it makes homes)",
    ("instance/management/commands/backup_instance.py", "remove_example_homes"): SEES_EVERY_HOME
    + " (it removes them from a copy)",
}


def _calls_a_filter(scope: ast.AST) -> bool:
    for node in ast.walk(scope):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name in FILTERS:
                return True
    return False


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}


def _scope(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> tuple[str, ast.AST | None]:
    """The dotted name of the function or class that holds `node`, and its outer function."""
    names: list[str] = []
    outer: ast.AST | None = None
    while node in parents:
        node = parents[node]
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.append(node.name)
            if not isinstance(node, ast.ClassDef):
                outer = node
    return ".".join(reversed(names)) or "<module>", outer


def python_files(root: Path) -> Iterator[Path]:
    for path in sorted(root.rglob("*.py")):
        if "migrations" not in path.parts:
            yield path


def reads_without_a_filter(root: Path) -> list[tuple[str, str]]:
    """(file, function) of each read of the homes that the function does not filter."""
    found: set[tuple[str, str]] = set()
    for path in python_files(root):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents = _parents(tree)
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Attribute)
                and node.attr in MANAGERS
                and isinstance(node.value, ast.Name)
                and node.value.id in MODELS
            ):
                continue
            parent = parents.get(node)
            if isinstance(parent, ast.Attribute) and parent.attr in CREATIONS:
                continue
            where, outer = _scope(node, parents)
            if outer is not None and _calls_a_filter(outer):
                continue
            found.add((path.relative_to(root).as_posix(), where))
    return sorted(found)


def _is_true(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def include_example_outside(root: Path, allowed: tuple[str, ...] = INCLUDE_EXAMPLE_IN) -> list[str]:
    """Where `include_example=True` is said, outside the callers the record names."""
    found = []
    for path in python_files(root):
        relative = path.relative_to(root).as_posix()
        if relative.startswith(allowed):
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            said = (isinstance(node, ast.keyword) and node.arg == "include_example") and _is_true(
                node.value
            )
            assigned = (
                isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "include_example" for t in node.targets)
                and _is_true(node.value)
            )
            if said or assigned:
                found.append(f"{relative}:{node.lineno}")  # type: ignore[attr-defined]
    return sorted(found)


def sets_the_mark_outside(root: Path, allowed: tuple[str, ...] = SETS_THE_MARK_IN) -> list[str]:
    """Where a row is made or changed with `is_example=True`, or `.is_example = True` is set."""
    found = []
    for path in python_files(root):
        relative = path.relative_to(root).as_posix()
        if relative.startswith(allowed):
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
                for keyword in node.keywords:
                    if (
                        keyword.arg == "is_example"
                        and name in WRITERS
                        and isinstance(keyword.value, ast.Constant)
                        and keyword.value.value is True
                    ):
                        found.append(f"{relative}:{node.lineno}")
            elif isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Attribute) and t.attr == "is_example" for t in node.targets
            ):
                found.append(f"{relative}:{node.lineno}")
    return sorted(found)


def test_every_read_of_the_homes_filters_or_is_audited() -> None:
    unaudited = [site for site in reads_without_a_filter(PACKAGE) if site not in AUDITED]
    assert unaudited == [], (
        "These functions read `Home` or `Membership` without `homes_for`, `homes_for_request` or "
        "`real_homes`: filter the read, or audit it in AUDITED with its reason: " + str(unaudited)
    )


def test_the_audited_reads_still_exist_and_each_says_why() -> None:
    present = set(reads_without_a_filter(PACKAGE))
    assert sorted(set(AUDITED) - present) == [], "an audited read is gone or now filters: remove it"
    assert all(len(reason) > 20 for reason in AUDITED.values())


def test_every_app_of_the_package_is_parsed_not_a_list_of_apps() -> None:
    parsed = {path.relative_to(PACKAGE).parts[0] for path in python_files(PACKAGE)}
    ours = {
        config.label
        for config in apps.get_app_configs()
        if config.name.startswith("housecairn.") and Path(config.path).is_relative_to(PACKAGE)
    }
    assert ours <= parsed, sorted(ours - parsed)
    assert {"homes", "guide", "sync", "notifications", "calendar", "labels"} <= parsed


def test_a_new_read_of_the_homes_fails_the_guard(tmp_path: Path) -> None:
    """The guard fails on the first commit of an app that reads the homes without filtering."""
    app = tmp_path / "newapp"
    app.mkdir()
    (app / "views.py").write_text(
        "from housecairn.homes.models import Home, Membership\n"
        "def all_homes_of_everyone():\n"
        "    return Home.objects.filter(name='x')\n"
        "def members():\n"
        "    return Membership.all_objects.all()\n"
        "def a_creation():\n"
        "    return Home.objects.create(name='x')\n"
        "def filtered(user):\n"
        "    from housecairn.homes.services import real_homes\n"
        "    return real_homes(Home.objects.all())\n"
        "def through_the_entry_point(user):\n"
        "    return homes_for(user).filter(pk=Home.objects.values('pk'))\n"
    )
    assert reads_without_a_filter(tmp_path) == [
        ("newapp/views.py", "all_homes_of_everyone"),
        ("newapp/views.py", "members"),
    ]


def test_include_example_is_said_only_where_the_record_allows() -> None:
    assert include_example_outside(PACKAGE) == []
    import tempfile

    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        (root / "labels").mkdir()
        (root / "labels" / "views.py").write_text(
            "homes_for(user, include_example=True)\nclass V:\n    include_example = True\n"
        )
        (root / "guide").mkdir()
        (root / "guide" / "views.py").write_text("homes_for(user, include_example=True)\n")
        assert include_example_outside(root) == ["labels/views.py:1", "labels/views.py:3"]


def test_nothing_but_the_service_that_makes_it_sets_the_mark() -> None:
    assert sets_the_mark_outside(PACKAGE) == []
    import tempfile

    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        (root / "views.py").write_text(
            "Home.objects.filter(is_example=True)\n"
            "Home(name='x', is_example=True)\n"
            "home.is_example = True\n"
            "Home.objects.filter(pk=1).update(is_example=True)\n"
        )
        assert sets_the_mark_outside(root) == ["views.py:2", "views.py:3", "views.py:4"]


@pytest.mark.parametrize("relative", sorted(path for path, _name in AUDITED))
def test_an_audited_file_exists(relative: str) -> None:
    assert (PACKAGE / relative).is_file()
