// The task form hides the fields its choices do not use (docs/screens/task-form.md); the
// item form (`data-in-place-form`) does the same for Add a contractor…
// First-party, no library. An element with `data-shown-when="name"` shows while the box
// `name` is ticked; with `data-shown-when="name=value"`, while the field `name` has that
// value. Without script nothing is hidden, and the server ignores what is not used.
// The due date has two fields (the first block, and the date a fixed repetition counts
// from): typing in one fills in the other.
// Inside a line of the materials (`data-scope="materials-3-"`) a rule names a field of the
// line. The buttons + and - add and remove lines there; without script the page offers
// "Add a line", which sends the form back with one more line.
(function () {
  "use strict";
  var form = document.querySelector("form[data-task-form], form[data-in-place-form]");
  if (!form) {
    return;
  }

  function value(name) {
    var field = form.elements.namedItem(name);
    if (!field) {
      return "";
    }
    if (field.type === "checkbox") {
      return field.checked ? "on" : "";
    }
    return field.value || "";
  }

  function update() {
    form.querySelectorAll("[data-shown-when]").forEach(function (element) {
      var rule = element.getAttribute("data-shown-when").split("=");
      var scope = element.closest("[data-scope]");
      var current = value((scope ? scope.getAttribute("data-scope") : "") + rule[0]);
      element.hidden = rule.length > 1 ? current !== rule[1] : current === "";
    });
  }

  function mirror(source, target) {
    source.addEventListener("input", function () {
      if (target.value !== source.value) {
        target.value = source.value;
      }
    });
  }

  var due = form.elements.namedItem("due_on");
  var start = form.elements.namedItem("repeat_start");
  if (due && start) {
    mirror(due, start);
    mirror(start, due);
  }
  var lines = form.querySelector("[data-material-lines]");
  var total = form.elements.namedItem("materials-TOTAL_FORMS");
  var initial = form.elements.namedItem("materials-INITIAL_FORMS");
  var template = form.querySelector("template[data-material-template]");
  var add = form.querySelector("[data-material-add]");
  var more = form.querySelector("[data-material-more]");
  var limit = form.querySelector("[data-material-limit]");

  function renumber(line, index, from) {
    var prefix = lines.getAttribute("data-prefix") + "-" + index + "-";
    ["name", "id", "for"].forEach(function (attribute) {
      line.querySelectorAll("[" + attribute + "]").forEach(function (element) {
        element.setAttribute(attribute, element.getAttribute(attribute).split(from).join(prefix));
      });
    });
    line.setAttribute("data-scope", prefix);
    var number = line.querySelector("[data-line-number]");
    if (number) {
      number.textContent = String(index + 1);
    }
  }

  function count() {
    return lines.querySelectorAll("[data-material-line]").length;
  }

  function refresh() {
    var full = count() >= parseInt(lines.getAttribute("data-max"), 10);
    total.value = String(count());
    if (initial) {
      initial.value = String(Math.min(parseInt(initial.value, 10) || 0, count()));
    }
    add.disabled = full;
    limit.textContent = full ? limit.getAttribute("data-limit-text") : "";
  }

  if (lines && total && template && add) {
    add.hidden = false;
    if (more) {
      more.remove();
    }
    form.querySelectorAll("[data-material-nojs]").forEach(function (note) {
      note.remove();
    });
    lines.querySelectorAll("[data-material-remove]").forEach(function (button) {
      button.hidden = false;
    });
    refresh();
    add.addEventListener("click", function () {
      if (add.disabled) {
        return;
      }
      var line = template.content.firstElementChild.cloneNode(true);
      renumber(line, count(), line.getAttribute("data-scope"));
      line.querySelector("[data-material-remove]").hidden = false;
      lines.appendChild(line);
      refresh();
      update();
      var first = line.querySelector("select, input");
      if (first) {
        first.focus();
      }
    });
    lines.addEventListener("click", function (event) {
      var button = event.target.closest("[data-material-remove]");
      if (!button) {
        return;
      }
      button.closest("[data-material-line]").remove();
      lines.querySelectorAll("[data-material-line]").forEach(function (line, index) {
        renumber(line, index, line.getAttribute("data-scope"));
      });
      refresh();
      add.focus();
    });
  }
  form.addEventListener("change", update);
  update();
})();
