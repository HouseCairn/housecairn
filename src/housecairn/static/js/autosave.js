// Save the sheet of a record field by field (docs/adr/0082, core/autosave.py).
// A form marked `data-autosave` sends itself through HTMX when a field changes, or after a
// pause in typing: the same POST as its button, with `_autosave=1`. One request at a time,
// the last state wins; a file field waits for the button. Without this script the button saves.
(function () {
  "use strict";
  var PAUSE = 800;

  function setUp(form) {
    var status = document.querySelector("[data-autosave-status]");
    var timer = null;
    if (status) status.hidden = false;

    function skipped(field) {
      return !field || !field.name || field.type === "file" || field.closest("[data-autosave-skip]");
    }

    // A field the browser already refuses (a required name left empty) is not sent: its
    // message goes under it and the status says so, as the server would.
    function refused() {
      var bad = false;
      Array.prototype.forEach.call(form.elements, function (field) {
        if (!field.name || !field.willValidate) return;
        var place = document.getElementById("autosave-error-" + field.name);
        var valid = field.checkValidity();
        if (place) {
          place.textContent = valid ? "" : field.validationMessage;
          place.hidden = valid;
        }
        bad = bad || !valid;
      });
      if (bad && status) status.textContent = status.getAttribute("data-failed-text") || "";
      return bad;
    }

    function send() {
      timer = null;
      if (!window.htmx || refused()) return;
      window.htmx.ajax("POST", form.getAttribute("action") || window.location.pathname + window.location.search, {
        source: form,
        values: { _autosave: "1" },
        target: "#autosave-status",
        swap: "innerHTML",
      });
    }

    form.addEventListener("input", function (event) {
      if (skipped(event.target)) return;
      if (timer) window.clearTimeout(timer);
      timer = window.setTimeout(send, PAUSE);
    });
    form.addEventListener("change", function (event) {
      if (skipped(event.target)) return;
      if (timer) window.clearTimeout(timer);
      send();
    });
  }

  function start() {
    document.querySelectorAll("form[data-autosave]").forEach(setUp);
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
