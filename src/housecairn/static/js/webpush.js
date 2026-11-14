/* Notifications on this device (docs/adr/0066, docs/adr/0072). Two entry points, one way to
   subscribe: the form of the Notifications screen ("Receive on this device", with a name
   typed by the person) and the offer on Today ("Receive the reminders on this device?",
   with a name made from the browser). The permission is asked in the tap that starts
   `enroll`, before anything is awaited, and never on load. The subscription of the browser
   goes to the API with the session and its form token. Nothing else is sent. */
"use strict";

(function () {
  var DAY = 24 * 60 * 60 * 1000;
  var LATER = 7 * DAY;
  var NAME_MAX = 60;

  function token() {
    try {
      return JSON.parse(document.body.getAttribute("hx-headers") || "{}")["X-CSRFToken"] || "";
    } catch (error) {
      return "";
    }
  }

  function key(text) {
    const padded = (text + "===".slice((text.length + 3) % 4)).replace(/-/g, "+").replace(/_/g, "/");
    return Uint8Array.from(atob(padded), (c) => c.charCodeAt(0));
  }

  function supported() {
    return "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
  }

  /* "ok", "unsupported", "denied" or "failed". Nothing is saved unless it is "ok". */
  async function enroll(config, name) {
    if (!supported()) {
      return "unsupported";
    }
    const permission = await Notification.requestPermission();
    if (permission !== "granted") {
      return "denied";
    }
    const registration = await navigator.serviceWorker.ready;
    /* A subscription that exists before the tap is not ours to give back if the server
       refuses: it may be the device that already receives the reminders. */
    const existing = await registration.pushManager.getSubscription();
    const subscription = await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: key(config.key),
    });
    const data = subscription.toJSON();
    const response = await fetch(config.endpoint, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": token() },
      body: JSON.stringify({
        name: name,
        endpoint: data.endpoint,
        p256dh: data.keys.p256dh,
        auth: data.keys.auth,
      }),
    });
    if (!response.ok) {
      if (!existing) {
        try {
          await subscription.unsubscribe();
        } catch (error) {
          // The browser keeps a subscription the server does not know: harmless.
        }
      }
      return "failed";
    }
    return "ok";
  }

  /* ---------- the name of a device, made from the browser ---------- */

  function isIos() {
    const ua = navigator.userAgent;
    return /iPhone|iPod|iPad/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1);
  }

  function browserOf(ua) {
    if (/Edg(e|A|iOS)?\//.test(ua)) return "Edge";
    if (/OPR\/|Opera/.test(ua)) return "Opera";
    if (/SamsungBrowser/.test(ua)) return "Samsung Internet";
    if (/Firefox\/|FxiOS/.test(ua)) return "Firefox";
    if (/Chrome\/|Chromium|CriOS/.test(ua)) return "Chrome";
    if (/Safari\//.test(ua)) return "Safari";
    return "";
  }

  function systemOf(ua) {
    if (/Android/.test(ua)) return "Android";
    if (/Windows/.test(ua)) return "Windows";
    if (/CrOS/.test(ua)) return "ChromeOS";
    if (/Macintosh|Mac OS X/.test(ua)) return "macOS";
    if (/Linux|X11/.test(ua)) return "Linux";
    return "";
  }

  /* "iPhone", "iPad", "Chrome on Android", "Firefox on Windows"; the pattern and the fallback
     are the page's own words (translated), the names of products are not. */
  function deviceName(ua, ios, pattern, fallback) {
    let name;
    if (ios) {
      name = /iPad/.test(ua) || /Macintosh/.test(ua) ? "iPad" : "iPhone";
    } else {
      const browser = browserOf(ua);
      const system = systemOf(ua);
      if (browser && system) {
        name = pattern.replace("{browser}", browser).replace("{system}", system);
      } else {
        name = browser || system || fallback;
      }
    }
    return name.slice(0, NAME_MAX);
  }

  function installed() {
    return (
      navigator.standalone === true ||
      (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches)
    );
  }

  /* ---------- the form of the Notifications screen ---------- */

  function show(form, name) {
    form.querySelectorAll("[data-webpush-unsupported], [data-webpush-denied], [data-webpush-failed]").forEach((node) => {
      node.hidden = !node.hasAttribute(name);
    });
  }

  document.querySelectorAll("form[data-webpush]").forEach((form) => {
    const blocked = form.querySelector("[data-webpush-blocked]");
    if (blocked && "Notification" in window && Notification.permission === "denied") {
      blocked.hidden = false;
    }
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      if (!form.reportValidity()) {
        return;
      }
      enroll(form.dataset, form.elements.name.value)
        .then((outcome) => {
          if (outcome === "ok") {
            window.location.reload();
          } else {
            show(form, outcome === "failed" ? "data-webpush-failed" : outcome === "denied" ? "data-webpush-denied" : "data-webpush-unsupported");
          }
        })
        .catch(() => show(form, "data-webpush-failed"));
    });
  });

  /* ---------- the offer on Today ---------- */

  function storage(name) {
    return name + ":" + document.querySelector("[data-webpush-offer]").dataset.person;
  }

  function read(name) {
    try {
      return window.localStorage.getItem(storage(name));
    } catch (error) {
      return null;
    }
  }

  function write(name, value) {
    try {
      window.localStorage.setItem(storage(name), String(value));
    } catch (error) {
      // Storage refused (private window): the offer comes back at the next page.
    }
  }

  async function hash(text) {
    const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
    return Array.from(new Uint8Array(bytes), (b) => b.toString(16).padStart(2, "0")).join("");
  }

  /* Whether to offer at all, from what only the browser knows. */
  async function wanted(offer, ios) {
    if (read("housecairn:webpush:never")) {
      return false;
    }
    if (Number(read("housecairn:webpush:later")) > Date.now()) {
      return false;
    }
    if (ios && !installed()) {
      return true; // Safari offers nothing before the application is installed: say how.
    }
    if (!supported() || Notification.permission === "denied") {
      return false;
    }
    const registration = await navigator.serviceWorker.ready;
    const subscription = await registration.pushManager.getSubscription();
    if (subscription) {
      const known = (offer.dataset.known || "").split(" ");
      if (known.indexOf(await hash(subscription.endpoint)) !== -1) {
        return false; // this browser is already one of the person's devices
      }
    }
    return true;
  }

  function setup(offer) {
    const ios = isIos();
    const ask = offer.querySelector("[data-offer-ask]");
    const install = offer.querySelector("[data-offer-install]");
    const messages = ["done", "denied", "failed"];

    function say(which) {
      messages.forEach((m) => {
        offer.querySelector("[data-offer-" + m + "]").hidden = m !== which;
      });
    }

    function leave() {
      offer.hidden = true;
    }

    offer.querySelectorAll("[data-offer-later]").forEach((button) => {
      button.addEventListener("click", () => {
        write("housecairn:webpush:later", Date.now() + LATER);
        leave();
      });
    });
    offer.querySelectorAll("[data-offer-never]").forEach((button) => {
      button.addEventListener("click", () => {
        write("housecairn:webpush:never", "1");
        leave();
      });
    });

    const enable = offer.querySelector("[data-offer-enable]");
    enable.addEventListener("click", () => {
      enable.disabled = true;
      const name = deviceName(navigator.userAgent, ios, offer.dataset.namePattern, offer.dataset.nameFallback);
      enroll(offer.dataset, name)
        .catch(() => "failed")
        .then((outcome) => {
          if (outcome === "ok") {
            ask.hidden = true;
            say("done");
          } else {
            say(outcome === "denied" ? "denied" : "failed");
            enable.disabled = false;
          }
        });
    });

    wanted(offer, ios)
      .then((yes) => {
        if (!yes) {
          return;
        }
        if (ios && !installed()) {
          install.hidden = false;
        } else {
          ask.hidden = false;
        }
        offer.hidden = false;
      })
      .catch(() => {
        // The browser cannot say (no service worker): no offer.
      });
  }

  document.querySelectorAll("[data-webpush-offer]").forEach(setup);
})();
