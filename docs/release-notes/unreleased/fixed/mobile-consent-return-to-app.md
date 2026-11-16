---
orphan: true
nosearch: true
---

- **Mobile app sign-in.** After Connect or Cancel on the consent page, the browser now returns to the mobile app. The page's Content Security Policy (`form-action`) blocked the redirect to the app, so the browser stayed on the site, while Connect had already connected the phone and sent its security email. Only the consent page allows the app's scheme; the other pages keep their policy.
