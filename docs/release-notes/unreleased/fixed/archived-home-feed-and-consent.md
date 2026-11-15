---
orphan: true
nosearch: true
---

- **No new calendar feed for an archived home.** Creating a feed of an archived home is now
  refused, from the page as from the API, as the page already offered none; a feed it has keeps
  working as before. See [Calendar and feeds](../../../guides/calendar.md).
- **Connecting a phone.** A link to the consent page with `approval_prompt=auto` showed an error
  page; it now shows the consent page, which is never skipped. The email that says a phone was
  connected is sent only once the server has given the phone its code.
