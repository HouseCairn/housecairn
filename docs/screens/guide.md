# Guide

The interactive guide, at `/guide/`, opened from **Guide** in the Help group of [More](more.md),
from the [welcome page](start-page.md) and from an empty [Today](today.md). It lists five
scenarios, each a short walk done for real on an **example home** with fictional values
([ADR 0084](../adr/0084-interactive-guide-and-example-home.md)); the
[Guide](../getting-started/guide.md) page of this documentation prints the same text.

Each scenario opens its [steps](guide-scenario.md) and says how far it is: **1 of 3**, then
**Done** once every step is ticked (an optional step does not count). The ticks are read from
the example home, so a person with no example home sees every scenario at **0**. The guide is
the same for every role and every account; it shows only your own example home.

Once the example home exists, a card under the scenarios names it, with a link to its
[page](home.md), and the button **Delete the example home**.

## Delete the example home

**Delete the example home** is on this page and in the banner of every page of the example
home. Pressing it shows, in place of the button, the question **Delete the example home?**
with **Delete** and **Cancel**: there is no page of its own, no name to type and no offer to
export, since the example home never leaves the instance. Without script, the question is the
same page with `?delete-example=1` in its address.

**Delete** erases the home, everything in it (its rooms, tasks, reports, projects, files) and
its ticks, and lands on the guide, or back on the guide page it was pressed from; **Cancel**
closes the question. You can then make a new example home from the first scenario. Only your
own example home goes this way: the address of a real home, or of the example home of
another account, answers **Page not found**, as for a home that does not exist.
