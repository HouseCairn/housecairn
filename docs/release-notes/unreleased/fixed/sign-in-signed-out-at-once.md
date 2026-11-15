---
orphan: true
nosearch: true
---

- **Signed out right after signing in.** On a slow server, signing in with the password could
  land back on the sign-in page, under "Successfully signed in": a request the sign-in page had
  started by itself (the passkey offered in the address field, or the offline pages the
  installable application keeps) answered after the sign-in with the session of before. The
  passkey request is now cancelled when the form is sent, and the offline pages are kept
  without the session.
