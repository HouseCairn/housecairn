# Invitation

The page an invitation link opens, at `/invitations/<link>/`. It names the home
and the role you are invited with.

- **No account yet**: choose a password and **Create the account and join**.
  The account takes the invited address, which the link has just proved. This
  works for a link that came by email; a link the person who invited you
  passed on by hand cannot create an account (the page says so, and says
  nothing about whether the address has one): sign in with the invited
  address, or ask an administrator of the instance.
- **An account already**: **Sign in**, then come back to the link and **Join
  the home**. You can also continue with the instance's single sign-on, if the
  provider confirms the invited address.
- **A link passed on by hand** proves nothing about the address: to join, your
  account must have verified the invited address. If it has not, the page says
  so instead of showing **Join the home**: verify the address from **Verify
  the address** (the email addresses of your account), then open the link
  again. An address that is not verified cannot become your primary address.
  Without outgoing email, the instance cannot verify it: ask its
  administrator, or ask to be invited again once the instance sends email. A
  link that came by email proves the address by itself.

An invitation to a home that was archived since waits: the page says that it will
work again once the home is brought back, and offers neither to join nor to create an
account. The link stays valid until then, within its expiry.

The link works once. A link that was used, revoked, replaced by a newer one or
has expired opens a page saying it no longer works, without any detail: ask the
person who invited you to send it again. Only the account with the invited
address can accept; signed in with another one, sign out first.
