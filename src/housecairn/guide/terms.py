"""The glossary terms that the texts of the guide cite, as the screens spell them.

A text of `guide.json` cites a label of a screen as `{{id}}`, a term of `design/glossary.json`
(docs/adr/0084). The image has no `design/` directory, so the site keeps here, for each term
the guide cites, the message of the catalog that the glossary names for the web;
`tests/test_guide_screens.py` keeps this table equal to the glossary. The term is then written
by the catalog of the screen, in the language of the reader, as the screen itself writes it.
"""

import re

from django.utils.html import escape, format_html
from django.utils.safestring import SafeString, mark_safe
from django.utils.translation import gettext, pgettext

# Term id -> (context, message) of its web catalog; the context is "" when there is none.
TERMS: dict[str, tuple[str, str]] = {
    "amount-planned": ("amount", "Planned"),
    "budget": ("", "Budget"),
    "committed": ("", "Committed"),
    "done": ("task", "Done"),
    "envelope": ("", "Envelope"),
    "paid": ("", "Paid"),
    "projects": ("", "Projects"),
    "quotes": ("", "Quotes"),
    "repeat": ("", "Repeat"),
    "report-a-problem": ("", "Report a problem"),
    "rooms": ("", "Rooms"),
    "today": ("", "Today"),
    "undo": ("", "Undo"),
    "what-is-wrong": ("", "What is wrong?"),
}

CITED = re.compile(r"\{\{([a-z0-9-]+)\}\}")


def term(term_id: str) -> str:
    """The label of the term in the active language, as its screen writes it."""
    context, message = TERMS[term_id]
    return pgettext(context, message) if context else gettext(message)


def render(text: str) -> SafeString:
    """A text of the guide, escaped, its cited terms in bold."""
    parts = []
    last = 0
    for match in CITED.finditer(text):
        parts.append(escape(text[last : match.start()]))
        parts.append(format_html("<strong>{}</strong>", term(match[1])))
        last = match.end()
    parts.append(escape(text[last:]))
    return mark_safe("".join(parts))  # noqa: S308 - every part is escaped above
