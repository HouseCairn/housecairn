// The Scan button of a barcode field (docs/adr/0083). Without script, or without a camera,
// the field is typed by hand. The camera is read by the browser's BarcodeDetector when there is
// one, otherwise by the vendored ZXing library, loaded from the instance on the first scan.
(function () {
  "use strict";
  // The library is next to this script, under static/vendor/: its address comes from this
  // script's own, never from the page.
  var here = document.currentScript ? document.currentScript.src : "";
  var labels = document.getElementById("barcode-scan-labels");
  var fields = document.querySelectorAll("input[data-barcode-field]");
  if (!labels || !fields.length) {
    return;
  }
  var text = function (name) {
    return labels.getAttribute("data-" + name) || "";
  };

  // What the code is: asked once it is typed or scanned, shown under the field. The answer
  // only offers values; "Use these values" fills the fields that are still empty.
  function lookUp(field, box) {
    var code = digits(field.value);
    if (!window.htmx || code.length < 8 || code.length > 14) {
      box.textContent = "";
      return;
    }
    window.htmx.ajax("POST", labels.getAttribute("data-lookup-url"), {
      target: box,
      swap: "innerHTML",
      values: { ean: code, target: labels.getAttribute("data-target") },
    });
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest ? event.target.closest("[data-barcode-fill]") : null;
    if (!button) {
      return;
    }
    var form = button.closest("form");
    var card = button.closest(".barcode-found");
    if (!form || !card) {
      return;
    }
    card.querySelectorAll("[data-fill-field]").forEach(function (entry) {
      var input = form.elements.namedItem(entry.getAttribute("data-fill-field"));
      if (!input || typeof input.value !== "string" || input.value.trim() !== "") {
        return;
      }
      input.value = entry.getAttribute("data-fill-value");
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });
    button.disabled = true;
  });

  Array.prototype.forEach.call(fields, function (field) {
    var box = document.createElement("div");
    box.className = "barcode-lookup-box";
    field.insertAdjacentElement("afterend", box);
    field.addEventListener("change", function () {
      lookUp(field, box);
    });
  });

  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    return;
  }
  var FORMATS = ["ean_13", "ean_8", "upc_a", "upc_e", "itf"];
  var zxing = null;

  function loadZxing() {
    if (zxing) {
      return zxing;
    }
    zxing = new Promise(function (resolve, reject) {
      var script = document.createElement("script");
      script.src = new URL("../vendor/zxing/zxing-library.min.js", here).href;
      script.integrity = labels.getAttribute("data-zxing-integrity");
      script.onload = function () {
        resolve(window.ZXing);
      };
      script.onerror = reject;
      document.head.appendChild(script);
    });
    return zxing;
  }

  function digits(value) {
    return String(value || "").replace(/\D/g, "");
  }

  function scan(field) {
    var dialog = document.createElement("dialog");
    dialog.className = "barcode-scan";
    var video = document.createElement("video");
    video.setAttribute("playsinline", "");
    video.muted = true;
    var status = document.createElement("p");
    status.setAttribute("role", "status");
    status.textContent = text("aim");
    var close = document.createElement("button");
    close.type = "button";
    close.className = "button";
    close.textContent = text("close");
    dialog.append(video, status, close);
    document.body.appendChild(dialog);
    var stream = null;
    var stopped = false;
    var reader = null;
    function stop() {
      stopped = true;
      if (reader) {
        reader.reset();
      }
      if (stream) {
        stream.getTracks().forEach(function (track) {
          track.stop();
        });
      }
      dialog.close();
      dialog.remove();
      field.focus();
    }
    function found(value) {
      var code = digits(value);
      if (!code || stopped) {
        return;
      }
      field.value = code;
      field.dispatchEvent(new Event("input", { bubbles: true }));
      field.dispatchEvent(new Event("change", { bubbles: true }));
      stop();
    }
    close.addEventListener("click", stop);
    dialog.addEventListener("cancel", stop);
    dialog.showModal();
    if ("BarcodeDetector" in window) {
      navigator.mediaDevices
        .getUserMedia({ video: { facingMode: "environment" }, audio: false })
        .then(function (media) {
          stream = media;
          video.srcObject = media;
          return video.play();
        })
        .then(function () {
          var detector = new window.BarcodeDetector({ formats: FORMATS });
          (function look() {
            if (stopped) {
              return;
            }
            detector
              .detect(video)
              .then(function (codes) {
                if (codes.length) {
                  found(codes[0].rawValue);
                } else {
                  window.setTimeout(look, 250);
                }
              })
              .catch(function () {
                window.setTimeout(look, 250);
              });
          })();
        })
        .catch(function () {
          status.textContent = text("no-camera");
        });
      return;
    }
    loadZxing()
      .then(function (library) {
        reader = new library.BrowserMultiFormatReader();
        return reader.decodeFromVideoDevice(undefined, video, function (result) {
          if (result) {
            found(result.getText());
          }
        });
      })
      .catch(function () {
        status.textContent = text("no-camera");
      });
  }

  Array.prototype.forEach.call(fields, function (field) {
    var button = document.createElement("button");
    button.type = "button";
    button.className = "button secondary barcode-scan-button";
    button.textContent = text("scan");
    button.setAttribute("aria-label", text("scan-label"));
    button.addEventListener("click", function () {
      scan(field);
    });
    field.insertAdjacentElement("afterend", button);
  });
})();
