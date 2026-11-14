/* HouseCairn service worker (docs/adr/0065). Served by core.views.service_worker.

   Reading offline, never writing: Today and the task list of the current home, kept when
   they were last opened online, and the static files. Everything else goes to the network
   as without a service worker; a page that was not kept is the offline page.

   - Only GET. A write (POST and the rest) is never answered from a cache nor queued.
   - A page is kept only when the server says so: `HouseCairn-Offline: <key>` on a 200, not
     `no-store`, not redirected (core.offline). The key belongs to the person, the session
     and the home; keeping a page under one key drops the pages kept under any other.
   - Signing out (the POST to the sign-out address), `HouseCairn-Offline: purge` (signed
     out, 401, 403), or a signed-out page that says so (static/js/offline.js), drops every
     page kept.
   - The cache names start with the scope: two applications under two prefixes of one
     origin never touch each other's caches. */
"use strict";

const CONFIG = JSON.parse("{{ config|escapejs }}");
const NAME = "housecairn:" + CONFIG.scope + ":";
const STATIC = NAME + "static:" + CONFIG.version;
const PAGES = NAME + "pages:";
const KEY = /^[0-9a-f]{32}$/;

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(STATIC)
      .then((cache) => cache.addAll(CONFIG.precache))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((names) =>
        Promise.all(
          names
            .filter((name) => name.startsWith(NAME + "static:") && name !== STATIC)
            .map((name) => caches.delete(name)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});

// Drops every page kept, but the cache named `keep` (null: all of them).
function purgePages(keep) {
  return caches
    .keys()
    .then((names) =>
      Promise.all(
        names
          .filter((name) => name.startsWith(PAGES) && name !== keep)
          .map((name) => caches.delete(name)),
      ),
    );
}

self.addEventListener("message", (event) => {
  // Only a page of the same origin may ask (a purge only empties the caches of this
  // worker, and asking has no other effect).
  if (event.origin !== self.location.origin) {
    return;
  }
  if (event.data && event.data.type === "purge") {
    event.waitUntil(purgePages(null));
  }
});

function mustPurge(response) {
  return (
    response.status === 401 ||
    response.status === 403 ||
    response.headers.get(CONFIG.header) === CONFIG.purge
  );
}

// The key under which the page may be kept, or null when it must not be.
function keyOf(response) {
  if (response.status !== 200 || response.type !== "basic" || response.redirected) {
    return null;
  }
  if (/no-store/i.test(response.headers.get("Cache-Control") || "")) {
    return null;
  }
  const key = response.headers.get(CONFIG.header) || "";
  return KEY.test(key) ? key : null;
}

async function keep(key, url, response) {
  const name = PAGES + key;
  await purgePages(name);
  const cache = await caches.open(name);
  await cache.put(url, response);
}

// A kept page says it comes from the cache: offline.js shows the banner, writes are off.
async function fromTheCache(response) {
  const text = await response.text();
  return new Response(text.replace('data-served="network"', 'data-served="cache"'), {
    status: 200,
    headers: response.headers,
  });
}

function isStart(url) {
  return new URL(url).pathname === CONFIG.start;
}

async function keptPage(request) {
  const names = (await caches.keys()).filter((name) => name.startsWith(PAGES));
  for (const name of names) {
    const cache = await caches.open(name);
    let found = await cache.match(request, { ignoreVary: true });
    if (!found && isStart(request.url)) {
      // The start address opens Today of the current home, the one kept.
      const today = (await cache.keys()).find((kept) =>
        new URL(kept.url).pathname.endsWith("/today/"),
      );
      found = today ? await cache.match(today, { ignoreVary: true }) : undefined;
    }
    if (found) {
      return fromTheCache(found);
    }
  }
  return offlinePage(names);
}

// The offline page in the language of the pages kept, else of the browser, else the one of
// the instance: a person who chose a language reads it offline too (docs/adr/0073).
async function offlinePage(names) {
  let language = "";
  for (const name of names) {
    const cache = await caches.open(name);
    const kept = await cache.keys();
    const page = kept.length ? await cache.match(kept[0], { ignoreVary: true }) : undefined;
    language = (page && page.headers.get("Content-Language")) || "";
    if (language) {
      break;
    }
  }
  const browser = (self.navigator.language || "").split("-")[0].toLowerCase();
  const url = CONFIG.offlinePages[language] || CONFIG.offlinePages[browser] || CONFIG.offline;
  const page =
    (await caches.match(url, { cacheName: STATIC })) ||
    (await caches.match(CONFIG.offline, { cacheName: STATIC }));
  return page || Response.error();
}

async function navigate(event) {
  const request = event.request;
  let response;
  try {
    response = await fetch(request);
  } catch (error) {
    return keptPage(request);
  }
  if (mustPurge(response)) {
    event.waitUntil(purgePages(null));
  } else {
    const key = keyOf(response);
    if (key) {
      event.waitUntil(keep(key, request.url, response.clone()));
    }
  }
  return response;
}

// Static files have their content hash in their name: the cache first, then the network.
async function staticFile(request) {
  const cache = await caches.open(STATIC);
  const found = await cache.match(request);
  if (found) {
    return found;
  }
  const response = await fetch(request);
  if (response.status === 200 && response.type === "basic") {
    await cache.put(request, response.clone());
  }
  return response;
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (url.pathname === CONFIG.signOut && request.method === "POST") {
    // Signing out: drop the pages now, whatever page the sign-out leads to.
    event.waitUntil(purgePages(null));
  }
  if (request.method !== "GET") {
    return;
  }
  if (url.origin !== self.location.origin || !url.pathname.startsWith(CONFIG.scope)) {
    return;
  }
  if (request.mode === "navigate") {
    event.respondWith(navigate(event));
  } else if (url.pathname.startsWith(CONFIG.static)) {
    event.respondWith(staticFile(request));
  }
});

/* Notifications on the device (docs/adr/0066). The server sends JSON, encrypted for this
   browser: a title, a text and the path of the screen to open. The text is shown as text
   (a notification has no markup); the path opens only under the scope of this worker,
   anything else opens the start address. */

// The address a notification opens: a path of this application, never another origin.
function target(path) {
  let text = typeof path === "string" ? path : "";
  if (!text.startsWith("/")) {
    return new URL(CONFIG.start, self.location.origin).href;
  }
  if (!text.startsWith(CONFIG.scope)) {
    text = CONFIG.scope.replace(/\/$/, "") + text;
  }
  const url = new URL(text, self.location.origin);
  if (url.origin !== self.location.origin || !url.pathname.startsWith(CONFIG.scope)) {
    return new URL(CONFIG.start, self.location.origin).href;
  }
  return url.href;
}

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (error) {
    data = {};
  }
  const title = typeof data.title === "string" && data.title ? data.title : "HouseCairn";
  event.waitUntil(
    self.registration.showNotification(title, {
      body: typeof data.body === "string" ? data.body : "",
      icon: CONFIG.icon,
      badge: CONFIG.icon,
      data: { url: target(data.url) },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || target(CONFIG.start);
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((windows) => {
      for (const client of windows) {
        if (client.url === url && "focus" in client) {
          return client.focus();
        }
      }
      return self.clients.openWindow(url);
    }),
  );
});
