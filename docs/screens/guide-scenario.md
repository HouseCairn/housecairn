# Guide scenario

One scenario of the [guide](guide.md), at `/guide/<scenario>/` (for example
`/guide/rooms-equipment/`): its title, what it is for, then its steps, three to six. Its link
at the top leads back to the guide; at the bottom, the previous and the next scenario.

## A step

A step has a title, one sentence and the buttons that lead to its screen, in the example home:

- **Fill in for me** opens the form of the step with the values of the example already in it
  (the name and the type of the room, the brand and the model of the machine, the amount of a
  quote). Nothing is saved yet: you press the button of the form yourself, and the form checks
  what it holds as if you had typed it.
- **Open** opens the same form empty, or the screen of a step that has no form (the page of the
  home, Today, a report).

When the row a step names is already in the example home (the coffee machine, for its brand and
model), the step opens the form of that row instead of a form that adds one. A budget line or a
quote waits for its project: until the project exists, the step has no button.

Once saved, a form opened from a step comes back to this page, as a form opened from any other
page goes back to it ([design guide](../developer/design-guide.md), "After a form"). The step
is then ticked, with **Done**, as soon as the data of the example home says it is done: a room
added ticks **Add a room**, whoever added it and from whichever screen. A step once ticked stays
ticked, even if you delete the room afterwards.

Four steps leave no trace in the data (look at the settings, find your way around, undo a
mistake, open the report): each has **Mark as done**, which ticks it.

The first step of the first scenario opens [Add a home](new-home.md) filled in with the example
home; with no example home, the steps of the other scenarios have no button yet.

Only the forms of the example home, and Add a home when the guide opens it, are filled in from
their address. On a real home the address is ignored: a link cannot put a value in front of you
there, nor in an edit form that saves as you type.

## Add what the earlier scenarios make

A scenario after the first one shows **Add what the earlier scenarios make** while an earlier
step that ticks by itself is not ticked, or while there is no example home. The button makes the
example home if needed, then adds to it what the earlier scenarios make and it lacks (for the
third scenario: the kitchen, the bathroom and the coffee machine), in your language, so that you
can start where you want. It never adds anything of the scenario you are on or of a later one,
so it ticks nothing ahead.
