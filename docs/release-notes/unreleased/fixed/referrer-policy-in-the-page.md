---
orphan: true
nosearch: true
---

- **Forms work again behind a proxy that replaces the referrer policy.** Through the Home
  Assistant app, every form, the first-account screen included, got "Session expired": Home
  Assistant gives its pages `Referrer-Policy: no-referrer`, and the browser then posts forms
  with `Origin: null`. Each page now states HouseCairn's policy itself
  ([Reverse proxy](../../../administration/reverse-proxy.md)).
