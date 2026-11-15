# Instance settings

The settings of the instance, reached from **Instance administration**. Administrators
only; anyone else gets a 404.

`/instance/settings/` is an **index**: one line per section, with the number of settings
and how many a variable sets, then **Server**. Each section has its own page,
`/instance/settings/<section>/` (for example `/instance/settings/rate-limits/`), with a
single button that names what it saves, such as **Save the rate limits**. A page saves
only its own settings; another section's fields sent with it are ignored. The old
addresses still work: `/instance/settings/?section=<section>` opens the page of the
section.

The link at the top of a section leads back to **Instance settings**.

Each setting is paired with an environment variable (see [Settings](../administration/settings.md)):

- **Editable**: no variable sets it. Change the value and press the button at the bottom of
  the page of its section. The default is shown below the field; **Restore the default** appears when you
  have changed it.
- **Locked**: a variable sets it. The field is read-only and shows the value in force, the
  name of the variable and a link to the documentation.
- **Secret** (the SMTP password): write only. The screen says whether one is stored; leave
  the field empty to keep it.

Sections (the index lists them): **Public address**, **Name, language and time zone** (name of the instance,
default language, time zone), **Sign-up** (open sign-up, sign-up through single sign-on,
allowed and blocked domains, anti-robot work), **Invitations** (lifetime), **Outgoing
email** (server, port, STARTTLS or TLS, user name, password, timeout, sender), **Rate
limits** (see [Rate limits](../administration/rate-limits.md)) and **Files of a home**
(largest file, space and number of files of each home). **Test the connection** checks the
mail server with the settings in force and sends nothing.
**Server**, the last line of the index, lists the variables that stay in the environment,
read-only.

**Notifications** turns reminders on or off for the whole instance (off by default) and sets
how often the background worker looks for them, from 1 to 60 minutes (see
[Notifications](../guides/notifications.md)). **Channels on the local network** (off by
default) lets channels send to private network addresses, for a ntfy or a Gotify next to
the instance; any person with an account can then make the server call such an address
(see [Hardening](../administration/hardening.md)). Loopback, link-local and cloud metadata
addresses stay refused either way. The section shows no channel of anyone.

**Projects** has one switch, **Preview of inspiration links** (off by default). On, the server
reads the page of a link once, in the background just after someone adds it to a project, to find its title and its
picture (see [Projects and quotes](../guides/projects.md)). It only calls public addresses:
never a private network nor a cloud metadata service.

**Mobile app** has one switch, **Mobile app** (on by default): people may connect the HouseCairn
mobile app to their account ([Connected apps](api-tokens.md#connected-apps)). Off, no phone can
connect or sync; the connected phones stay listed and work again when it is turned back on (see
[Settings](../administration/settings.md#mobile-app)).

Saving asks you to have signed in within the last five minutes; if you have not, you
are sent to confirm your sign-in and then back to the same page, and you make the change again.
Every change is in the [audit log](instance.md).
