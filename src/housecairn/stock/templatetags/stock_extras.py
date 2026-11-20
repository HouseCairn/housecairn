"""Stock on the pages of other apps: the room page shows the articles that concern it."""

from decimal import Decimal
from typing import Any

from django import template
from django.template.defaultfilters import floatformat

from housecairn.rooms.models import Item, Room
from housecairn.stock import services
from housecairn.stock.models import Article

register = template.Library()


@register.filter
def amount(article: Article, quantity: Decimal) -> str:
    """A quantity of the article with its unit: "2.5 litres", "1 box", "3"."""
    number = floatformat(quantity, "-2")
    unit = article.unit_for(quantity)
    return f"{number} {unit}" if unit else number


@register.filter
def unit_after(article: Article, quantity: Decimal) -> str:
    """The unit as it reads after the quantity, or nothing."""
    return article.unit_for(quantity)


@register.simple_tag
def room_articles(room: Room) -> list[dict[str, Any]]:
    """The articles that concern the room, each with its quantity and whether it is low."""
    return [
        {"article": a, "low": services.is_low(a, a.quantity)}  # type: ignore[attr-defined]
        for a in services.articles_of_room(room.home, room.pk)
    ]


@register.simple_tag
def paint_article_of(room: Room) -> Article | None:
    """The tin of paint left over, with its quantity; none if there is none or it was deleted."""
    if room.paint_article_id is None:
        return None
    return services.articles_of(room.home).filter(pk=room.paint_article_id).first()


@register.simple_tag
def item_articles(item: Item) -> list[Article]:
    """The articles of the stock that fit the item, each with its quantity."""
    return list(services.articles_of_item(item))
