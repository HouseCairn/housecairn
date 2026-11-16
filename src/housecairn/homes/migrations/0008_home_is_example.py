from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("homes", "0007_home_archived_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="home",
            name="is_example",
            field=models.BooleanField(
                db_default=False, default=False, editable=False, verbose_name="Example home"
            ),
        ),
    ]
