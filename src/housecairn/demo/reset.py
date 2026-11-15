"""Back to the known state of a demonstration instance (docs/adr/0071).

Everything goes: the accounts, their homes and what they hold, the settings stored in the
interface, the sessions, the audit log and the uploaded files. Then the two public accounts
are created again, with the demonstration home of `load_demo_data`, whose dates are relative
to the day of the reset. The database part is one transaction; the files are removed first,
since a file cannot be part of a transaction.

The tables of the task worker (django-q2) stay: they hold the schedule of this reset and the
task that runs it. The content types and permissions of Django stay too: they describe the
code, not the data.
"""

import datetime
import logging
import shutil
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core.cache import cache
from django.core.management.color import no_style
from django.db import connection, transaction
from django.utils.translation import gettext as _

from housecairn.demo import mode

logger = logging.getLogger("housecairn.demo")

# Apps whose tables a reset keeps.
KEPT_APPS = frozenset({"django_q", "contenttypes"})
# The device id the server writes with is made once per instance (docs/adr/0077).
KEPT_MODELS = frozenset({"auth.permission", "sync.serverdevice"})


class DemoOffError(Exception):
    """`reset_demo` on an instance that is not a demonstration: nothing was touched."""


def _tables() -> list[str]:
    existing = set(connection.introspection.table_names())
    tables: list[str] = []
    for model in apps.get_models(include_auto_created=True):
        meta = model._meta
        if not meta.managed or meta.proxy or meta.app_label in KEPT_APPS:
            continue
        if meta.label_lower in KEPT_MODELS or meta.db_table not in existing:
            continue
        tables.append(meta.db_table)
    return sorted(set(tables))


def _remove_files() -> None:
    root = Path(settings.MEDIA_ROOT)
    if not root.is_dir():
        return
    for entry in root.iterdir():
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()


def _language() -> str:
    from housecairn.homes.management.commands.load_demo_data import LANGUAGES
    from housecairn.instance.config import get_setting

    language = str(get_setting("default_language"))
    return language if language in LANGUAGES else "en"


def is_empty() -> bool:
    from housecairn.accounts.models import User

    return not User.objects.exists()


def reset(*, language: str | None = None, today: datetime.date | None = None) -> None:
    """Empty the instance, then create the two public accounts and the demonstration home."""
    from allauth.account.models import EmailAddress

    from housecairn.accounts.models import User
    from housecairn.accounts.services import record_first_account
    from housecairn.homes.management.commands.load_demo_data import load
    from housecairn.homes.models import Membership, Role
    from housecairn.instance import config

    if not mode.is_on():
        raise DemoOffError(
            "HOUSECAIRN_DEMO is not true: reset_demo would erase a real instance, refused."
        )
    _remove_files()
    with mode.reset_context(), transaction.atomic():
        if connection.vendor == "postgresql":
            # PostgreSQL refuses to TRUNCATE a table with deferred foreign-key checks still
            # pending in the transaction: run them first.
            with connection.cursor() as cursor:
                cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        connection.ops.execute_sql_flush(
            connection.ops.sql_flush(no_style(), _tables(), allow_cascade=True)
        )
        config.invalidate()
        admin = User.objects.create_superuser(
            email=mode.ADMIN_EMAIL, password=mode.PASSWORD, display_name=_("Demo admin")
        )
        member = User.objects.create_user(
            email=mode.USER_EMAIL, password=mode.PASSWORD, display_name=_("Demo user")
        )
        for user in (admin, member):
            EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
        record_first_account(admin)
        home = load(admin, language or _language(), today or datetime.date.today())
        Membership.objects.create(home=home, user=member, role=Role.MEMBER)
    config.invalidate()
    cache.clear()
    logger.info("Demonstration instance reset.")
