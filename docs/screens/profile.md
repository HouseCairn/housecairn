# Profile

How you are named to the other members of your homes, at `/me/profile/` (**More**, then
**Profile**). One optional field:

- **Display name**: up to 60 characters, on one line. It is what people see on the tasks you
  are in charge of, in the [history](history.md), in [Today](today.md) and in the
  [household](household.md). It cannot contain an `@` (it would pass for an address) and must
  show at least one visible character. Where you pick a person (the assignee of a task, the
  "Done by" filter), two members of the same name are told apart by their address in brackets.

**From your sign-in provider.** If your account is linked to a sign-in provider (single
sign-on), the provider gives your display name: at each sign-in through it, HouseCairn takes
your `name` from the provider, or else your given and family names, your username or your
nickname, whichever comes first and is a valid display name. The field then says **Taken
from** and the name of the provider, and follows the provider when the name changes there.
A name the provider gives that would be refused here (an `@`, nothing visible, more than 60
characters, markup) is ignored: you keep the name you had, and the sign-in goes on.

Once you change the name yourself, it is yours: no provider changes it any more. **Use the
provider's name** gives it back to the provider: the name of your last sign-in through a
provider is used at once when HouseCairn still has it, otherwise at your next sign-in
through one. An account linked to two providers takes the name of the one it signs in
with. An account that is not linked to a provider is not concerned.

Without a display name you are shown by your full name if you have one, and otherwise by the
part of your email address before the `@`. Your whole address is never used to name you on
those screens; it appears only where the address is the subject (your account, an
invitation, the sheet of a member).

**Language** says the language your pages and messages are in, or **Automatic**, and
**Change the language** opens the [display preferences](display-preferences.md), where it is
set; **Save** there brings you back here.

The same field is in the [API](../api/index.md): `GET` and `PATCH /api/v1/me/`, with
`display_name_source` (`oidc` or `manual`; `oidc` is **Use the provider's name**) and
`display_name_provider`, the identifier of the provider the name was taken from.
