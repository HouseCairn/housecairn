"""One language per person (docs/adr/0073).

The notification preferences had their own language, for the reminders, the notifications
on a device and the calendar feeds, apart from the language of the pages. A person who had
saved one and had chosen no language for the pages keeps it, now as their language
everywhere; a person who had chosen one for the pages keeps theirs. The notification
preferences then lose their field (notifications 0004).
"""

from typing import Any

from django.conf import settings
from django.db import migrations


def copy_the_notification_language(apps: Any, schema_editor: Any) -> None:
    Preference = apps.get_model("notifications", "Preference")
    DisplayPreference = apps.get_model("tasks", "DisplayPreference")
    shipped = {code for code, _name in settings.LANGUAGES}
    chosen = set(
        DisplayPreference.objects.exclude(language="").values_list("user_id", flat=True)
    )
    for user_id, language in Preference.objects.values_list("user_id", "language"):
        if user_id in chosen or language not in shipped:
            continue
        display, _created = DisplayPreference.objects.get_or_create(user_id=user_id)
        display.language = language
        display.save(update_fields=["language"])


class Migration(migrations.Migration):
    dependencies = [
        ("tasks", "0011_estimate_up_to_a_year"),
        ("notifications", "0003_background_state"),
    ]

    operations = [
        migrations.RunPython(copy_the_notification_language, migrations.RunPython.noop),
    ]
