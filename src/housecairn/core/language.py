"""The language of a person, wherever they read HouseCairn (docs/adr/0072).

One rule for everything a person reads, online, offline and in what is sent to them:

1. the language they chose in their display preferences;
2. else, for a page or an API answer, the language the request asks for (the language
   cookie, then the browser);
3. else the default language of the instance.

A message sent without a request (a reminder, a notification on a device, a calendar feed,
an email sent by a worker) has no browser to ask: it takes the person's choice, then the
default of the instance.
"""

from django.conf import settings
from django.http import HttpRequest
from django.utils import translation

from housecairn.accounts.models import User


def shipped(code: object) -> bool:
    """Whether `code` is exactly one of the languages the instance ships."""
    return isinstance(code, str) and code in {code for code, _name in settings.LANGUAGES}


def instance_default() -> str:
    """The default language of the instance, or English if what is stored is not shipped."""
    from housecairn.instance.config import get_setting

    value = str(get_setting("default_language"))
    return value if shipped(value) else settings.LANGUAGE_CODE


def chosen_language(user_id: object) -> str:
    """The language the person chose, or "" for Automatic.

    The stored value is checked again: a value written behind the model's back (a restore, a
    script) counts as Automatic.
    """
    from housecairn.tasks.models import DisplayPreference

    if user_id is None:
        return ""
    chosen = (
        DisplayPreference.objects.filter(user_id=user_id).values_list("language", flat=True).first()
    )
    return str(chosen) if shipped(chosen) else ""


def language_of(user: User) -> str:
    """The language of what is sent to the person, with no request to ask: their choice,
    then the default of the instance."""
    return chosen_language(user.pk) or instance_default()


def activate_for(request: HttpRequest, user: User) -> None:
    """Make the person's choice the language of the rest of this request, if they made one."""
    chosen = chosen_language(user.pk)
    if chosen:
        translation.activate(chosen)
        request.LANGUAGE_CODE = translation.get_language()
