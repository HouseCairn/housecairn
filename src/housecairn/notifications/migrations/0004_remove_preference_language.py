"""The language of a person is in their display preferences only (docs/adr/0072)."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("notifications", "0003_background_state"),
        # It first copies the language into the display preferences.
        ("tasks", "0012_one_language_per_person"),
    ]

    operations = [
        migrations.RemoveField(model_name="preference", name="language"),
    ]
