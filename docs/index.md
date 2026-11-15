---
myst:
  html_meta:
    description: "HouseCairn plans your home's maintenance and renovation work, room by room: what to do, where, when, with what and for how much. Free, self-hosted, works with Home Assistant."
---

# HouseCairn

:::{container} hc-hero
**Plan your home's maintenance and renovation work, room by room.**

HouseCairn keeps the list of everything your house needs, from recurring upkeep
such as the smoke detector battery to projects such as repainting the living
room. For each job it knows the room, the date, the supplies and the planned
cost, and anyone in the household marks it done in one tap. It is free software
that you host yourself, works on its own, and connects to Home Assistant if you
use it.
:::

For example:

- test the smoke detector every six months, without remembering the last time;
- clean the ventilation filter every three months, with a warning when the
  spare filters run low;
- book the yearly boiler service, with the boiler's model and warranty at hand;
- repaint the living room, with its quotes and its budget, planned, committed
  and paid.

::::{container} hc-status
**Alpha**

HouseCairn is released as 0.x versions and has not been used in a real home for
any length of time yet: what it stores can still change between versions. Try
it, and keep a backup of what you enter. These pages describe only what exists
today.
::::

::::::{container} hc-cards
:::::{container} hc-card hc-card-start
[Get started](getting-started/index.md)

Install HouseCairn with Docker Compose and sign in for the first time.
:::::

:::::{container} hc-card hc-card-guides
[Guides](guides/index.md)

Tasks and recurrence, rooms and items, projects, budget, stock, step by step.
:::::

:::::{container} hc-card hc-card-screens
[Screen reference](screens/index.md)

Every screen of the application, with its help page.
:::::

:::::{container} hc-card hc-card-api
[API](api/index.md)

The REST API and its reference, generated from the code.
:::::

:::::{container} hc-card hc-card-admin
[Administration](administration/index.md)

Configuration, reverse proxy, single sign-on, backups and updates.
:::::

:::::{container} hc-card hc-card-security
[Security](security/index.md)

No telemetry, signed images, and how to report a vulnerability.
:::::
::::::

The website, <https://housecairn.app>, presents the project; the source code is
on [GitHub](https://github.com/HouseCairn/housecairn), with the
[Home Assistant app](https://github.com/HouseCairn/home-assistant-addons) and the
[Home Assistant integration](https://github.com/HouseCairn/home-assistant-housecairn).

```{toctree}
:hidden:

getting-started/index
guides/index
screens/index
api/index
administration/index
integrations/index
security/index
developer/index
release-notes
```
