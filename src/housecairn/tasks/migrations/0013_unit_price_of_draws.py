"""The unit price a stock draw was taken at (docs/adr/0090).

The real cost of a done task now counts the stock it used, at the unit price of each article
when it was taken. The draws made before this version kept no price: they get the unit price
the article has now, an approximation the release notes say. A draw whose article has no
price stays without one and counts for nothing. Undone draws get it too, so that a closing
made again after an undo of before finds a price, as a new draw would.
"""

from typing import Any

import django.core.validators
from django.db import migrations, models


def price_the_draws_of_before(apps: Any, schema_editor: Any) -> None:
    MaterialDraw = apps.get_model("tasks", "MaterialDraw")
    Article = apps.get_model("stock", "Article")
    priced = Article._base_manager.filter(unit_price__isnull=False)
    for article_id, price in list(priced.values_list("id", "unit_price")):
        MaterialDraw._base_manager.filter(article_id=article_id, unit_price__isnull=True).update(
            unit_price=price
        )


class Migration(migrations.Migration):
    dependencies = [
        ("tasks", "0012_one_language_per_person"),
        ("stock", "0005_article_unit_price"),
    ]

    operations = [
        migrations.AddField(
            model_name="materialdraw",
            name="unit_price",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                editable=False,
                max_digits=10,
                null=True,
                validators=[django.core.validators.MinValueValidator(0)],
                verbose_name="unit price when taken",
            ),
        ),
        migrations.RunPython(price_the_draws_of_before, migrations.RunPython.noop),
    ]
