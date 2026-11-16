"""Search and filters that follow what is typed or chosen (design/patterns.md, Filters).

A list page is a `GET` form around its fields. Without script the form is sent by Enter or by
its apply button, and the page reloads. With HTMX the same form asks for the same address
and swaps only `#results`, so that nothing is rebuilt that the person is typing in:

- `{% live_form %}` on the `<form>`: what is swapped (`#results`, and the count of the sheet of
  filters and the status line for screen readers, `hx-select-oob`), and the address, which is
  pushed so that Back and a bookmark keep the filter.
- `{% live_typing %}` on a search field: 300 ms after the last key, and only when the words
  changed, so a pause costs one request and a correction back to the same words none.
- `{% live_change %}` on a select, a date or a box: at the moment it is chosen.

Each request replaces the one still running (`hx-sync`). Nothing here runs the filter on the
server: the view answers a full page, and `hx-select` keeps the part that changed.
"""

from django import template
from django.utils.html import format_html
from django.utils.safestring import SafeString

register = template.Library()


@register.simple_tag(takes_context=True)
def live_form(context: template.Context) -> SafeString:
    return format_html(
        'hx-get="{}" hx-target="#results" hx-select="#results" hx-swap="outerHTML" '
        'hx-push-url="true" hx-select-oob="#filters-count,#results-status:innerHTML"',
        context["request"].path,
    )


def _control(context: template.Context, trigger: str) -> SafeString:
    return format_html(
        'hx-get="{}" hx-trigger="{}" hx-include="closest form" hx-sync="closest form:replace"',
        context["request"].path,
        trigger,
    )


@register.simple_tag(takes_context=True)
def live_typing(context: template.Context) -> SafeString:
    return _control(context, "input changed delay:300ms")


@register.simple_tag(takes_context=True)
def live_change(context: template.Context) -> SafeString:
    return _control(context, "change")
