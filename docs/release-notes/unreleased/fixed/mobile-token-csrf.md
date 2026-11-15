---
orphan: true
nosearch: true
---

- **Mobile app sign-in.** The token and revocation endpoints (`/o/token/` and `/o/revoke_token/`) no longer answer 403 when the mobile app calls them without a CSRF cookie, which kept the app from exchanging its code, refreshing its access or disconnecting. The consent page keeps its CSRF check.
