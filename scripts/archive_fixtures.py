"""Make the archives of `tests/fixtures/archives/`: one real export per published release.

`tests/test_restore_versions.py` restores each of them on an empty database, so that an
archive made by a release of before keeps restoring (docs/administration/data.md). Each
archive is made by the code of its own tag, not by `main` with its manifest rewritten:

    uv run python scripts/archive_fixtures.py            # every tag of TAGS
    uv run python scripts/archive_fixtures.py v0.5.2     # one tag

For each tag, the script unpacks the tree of the tag (`git archive`) into a temporary
folder, installs its locked dependencies there with uv (`uv sync --frozen`), migrates an
empty SQLite database, creates the account `owner@example.com`, fills it with
`load_demo_data` and `ADDITIONS` below, and writes the archive of the home with the export
of that tag. Every name is the invented one of the demo; no path or host name of the
machine goes into an archive. A new release adds its tag to TAGS and its archive here.
"""

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "archives"
TAGS = (
    "v0.1.0",
    "v0.2.0",
    "v0.3.0",
    "v0.3.1",
    "v0.3.2",
    "v0.4.0",
    "v0.4.1",
    "v0.5.0",
    "v0.5.1",
    "v0.5.2",
)

# Run by `manage.py shell` in the tree of the tag, after `load_demo_data`. What the demo of
# an early release lacks is added with the models of that release, so that every archive
# holds an item, a stock item and a done occurrence; and, where the release has them, an
# item whose contract provider is a text (format 2) and a draw of stock.
ADDITIONS = r"""
import datetime
import json
from decimal import Decimal
from pathlib import Path

from housecairn.accounts.models import User
from housecairn.homes import exports
from housecairn.homes.models import Home
from housecairn.rooms.models import Item, Room
from housecairn.stock.models import Article
from housecairn.tasks import services as tasks
from housecairn.tasks.models import Occurrence, Task

owner = User.objects.get(email="owner@example.com")
home = Home.objects.get()
item_fields = {f.name for f in Item._meta.get_fields()}
if "contract_provider" in item_fields:
    room = Room.objects.filter(home=home).order_by("position", "created_at").last()
    Item.objects.create(
        home=home, room=room, name="Boiler", contract_provider="Example Heating Services"
    )
if not Item.objects.filter(home=home).exists():  # pragma: no cover - every demo has a room
    Item.objects.create(home=home, room=Room.objects.filter(home=home).first(), name="Boiler")
if not Article.objects.filter(home=home).exists():
    Article.objects.create(home=home, name="Air filter")
# A task with a plan, done: format 4 keeps the plan of a done occurrence since 0.6.0.
task = Task.objects.filter(home=home, title__in=("Boiler yearly service",)).get()
Task.objects.filter(pk=task.pk).update(
    labour_budget=Decimal("80.00"), material_budget=Decimal("25.00")
)
open_ones = Occurrence.objects.filter(home=home, status="planned").order_by("due_on", "id")
tasks.complete(owner, open_ones.filter(task=task).get(), on=datetime.date.today())
try:
    from housecairn.tasks.models import TaskMaterial
except ImportError:  # releases before the materials of a task
    TaskMaterial = None
if TaskMaterial is not None:
    # A task whose materials the stock prices: its material budget is not the plan kept.
    for link in TaskMaterial.objects.filter(home=home, deleted_at__isnull=True):
        Task.objects.filter(pk=link.task_id).update(
            labour_budget=Decimal("15.00"), material_budget=Decimal("12.00")
        )
        planned = open_ones.filter(task_id=link.task_id).first()
        if planned is not None:
            tasks.complete(owner, planned, on=datetime.date.today())
handle, _name = exports.build_archive(home)
Path(TARGET).write_bytes(handle.read())
print(json.dumps({"made": TARGET}))
"""


def _run(command: list[str], **kwargs: object) -> None:
    subprocess.run(command, check=True, **kwargs)  # type: ignore[call-overload]  # noqa: S603


def make(tag: str, work: Path) -> Path:
    tree = work / tag
    tree.mkdir(parents=True)
    command = ["git", "-C", str(ROOT), "archive", tag]
    archive = subprocess.run(command, check=True, capture_output=True).stdout  # noqa: S603
    _run(["tar", "-x", "-C", str(tree)], input=archive)
    data = work / f"{tag}-data"
    data.mkdir()
    env = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "HOME", "TMPDIR", "UV_CACHE_DIR", "UV_PYTHON_INSTALL_DIR", "LANG"}
    }
    env.update(
        {
            "UV_PROJECT_ENVIRONMENT": str(tree / ".venv"),
            "DJANGO_SETTINGS_MODULE": "housecairn.settings",
            "DEBUG": "true",
            "SECRET_KEY": "archive-fixtures-only",
            "HOUSECAIRN_DATA_DIR": str(data),
        }
    )
    _run(["uv", "sync", "--frozen", "--no-dev", "--quiet"], cwd=tree, env=env)
    python = str(tree / ".venv" / "bin" / "python")
    _run([python, "manage.py", "migrate", "-v0"], cwd=tree, env=env)
    _run(
        [
            python,
            "manage.py",
            "shell",
            "-c",
            "from housecairn.accounts.models import User; "
            "User.objects.create_user(email='owner@example.com', password='Archive-fixture-1')",
        ],
        cwd=tree,
        env=env,
    )
    _run(
        [python, "manage.py", "load_demo_data", "--owner", "owner@example.com", "--language", "en"],
        cwd=tree,
        env=env,
    )
    target = FIXTURES / f"{tag}.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    code = f"TARGET = {str(target)!r}\n{ADDITIONS}"
    _run([python, "manage.py", "shell", "-c", code], cwd=tree, env=env)
    return target


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tags", nargs="*", default=list(TAGS))
    tags = parser.parse_args(argv).tags
    with tempfile.TemporaryDirectory(prefix="housecairn-archives-") as folder:
        for tag in tags:
            sys.stdout.write(f"{make(tag, Path(folder))}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
