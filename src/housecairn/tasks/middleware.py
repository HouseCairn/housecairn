"""The language a person chose in their display preferences (docs/adr/0042, 0072)."""

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

from housecairn.core.language import activate_for


class PersonLanguageMiddleware:
    """A signed-in person's own language wins over the cookie, the browser and the default.

    It runs after authentication, so after the locale middleware has chosen from the
    request; it only overrides that choice when the person saved one. A request
    authenticated by an API token is known only to the API view: the token authentication
    applies the same choice (apitokens.authentication). The stored value is checked again
    against the languages the instance ships: a value written behind the model's back (a
    restore, a script) falls back to the choice already made.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            activate_for(request, user)
        return self.get_response(request)
