---
orphan: true
nosearch: true
---

- **A new dependency and its tables.** This version adds django-oauth-toolkit (and
  jwcrypto, which it pulls in) to the image; its migrations and those of `apitokens`
  (`0003`, `0004`) run at start-up as usual and create the one client of the mobile app. A new
  setting, **Mobile app** (`HOUSECAIRN_MOBILE_APP_ENABLED`), on by default, turns the app off
  ([Settings](../../../administration/settings.md#mobile-app)).
- **Behind a reverse proxy.** The mobile app needs HTTPS, or plain HTTP between local addresses
  only, and the proxy must pass the original scheme and `Host`. A trusted proxy that sends no
  `X-Forwarded-For` leaves its own address as the client's
  ([Reverse proxy](../../../administration/reverse-proxy.md#the-mobile-app)).
