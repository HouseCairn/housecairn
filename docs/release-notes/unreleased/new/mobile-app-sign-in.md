---
orphan: true
nosearch: true
---

- **Connected apps.** The HouseCairn mobile app can connect to this server: it opens the
  server's own sign-in page in the phone's browser (OAuth 2 with PKCE), where you choose the
  homes the phone sees and whether it may create homes. Each phone is listed under
  **Connected apps** on the [API tokens](../../../screens/api-tokens.md#connected-apps) page,
  where you rename it, change its homes or revoke it; every new connection sends you an email.
  A connected phone opens only the sync API of the app, starting with
  `GET /api/v1/sync/info`, never the rest of the API.
