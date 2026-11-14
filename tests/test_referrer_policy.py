"""Every page states HouseCairn's referrer policy itself (docs/adr/0075).

A reverse proxy may replace the `Referrer-Policy` header: Home Assistant gives every response
of its server `no-referrer`, the ingress ones included. Under that policy a browser posts each
form with `Origin: null`, and Django's CSRF check refuses it ("Session expired"). The policy
written in the page wins over the header, so the browser sends the page's real origin again.
"""

import re
from collections.abc import Callable

import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse

from housecairn.accounts.models import User

pytestmark = pytest.mark.django_db

META = re.compile(r"<meta\s+name=\"referrer\"\s+content=\"([^\"]*)\"\s*/?>")


def policies_in_head(client: Client, url: str) -> list[str]:
    page = client.get(url)
    assert page.status_code == 200, url
    head = page.content.decode().split("</head>", 1)[0]
    return META.findall(head)


def test_the_page_and_the_header_say_the_same() -> None:
    assert settings.SECURE_REFERRER_POLICY == "same-origin"


def test_the_first_account_screen(client: Client) -> None:
    # The screen of issue #11 of the Home Assistant app: no administrator yet.
    assert policies_in_head(client, reverse("setup")) == [settings.SECURE_REFERRER_POLICY]


def test_a_signed_out_page(client: Client) -> None:
    assert policies_in_head(client, reverse("account_login")) == [settings.SECURE_REFERRER_POLICY]


def test_a_signed_in_page(client: Client, alice: User) -> None:
    client.force_login(alice)
    assert policies_in_head(client, reverse("profile")) == [settings.SECURE_REFERRER_POLICY]


def test_the_administration(client: Client, make_user: Callable[..., User]) -> None:
    client.force_login(make_user("admin@example.com", is_staff=True, is_superuser=True))
    for url in (reverse("admin:index"), reverse("admin:accounts_user_changelist")):
        assert policies_in_head(client, url) == [settings.SECURE_REFERRER_POLICY], url
