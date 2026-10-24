// Settings page (self-contained: the main page's helpers aren't loaded here).
const $ = (sel) => document.querySelector(sel);
let fields = [];

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  Object.assign(node, props);
  children.forEach((c) => node.append(c));
  return node;
}
function setMsg(text, kind) {
  $("#msg").textContent = text || "";
  $("#msg").className = "msg " + (kind || "");
}
async function api(method, url, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(url, opts);
  let data = {};
  try { data = await res.json(); } catch (_) {}
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

// ---------- environment check ----------
async function loadSystem() {
  const box = $("#system");
  box.replaceChildren(el("p", { className: "muted", textContent: "Checking..." }));
  try {
    const d = await api("GET", "/api/system");
    box.replaceChildren();
    d.checks.forEach((c) => {
      const row = el("div", { className: "check-row" });
      row.append(
        el("span", { className: "badge " + (c.ok ? "done" : "failed"), textContent: c.ok ? "OK" : "Problem" }),
        el("div", {}, el("strong", { textContent: c.name }), el("div", { className: "muted", textContent: c.detail })),
      );
      box.append(row);
    });
  } catch (e) {
    box.replaceChildren(el("p", { className: "msg error", textContent: e.message }));
  }
}
$("#recheck").onclick = loadSystem;

// ---------- settings form ----------
function buildInput(f) {
  let input;
  if (f.kind === "choice") {
    input = el("select");
    f.choices.forEach((c) => input.append(el("option", { value: c, textContent: c })));
  } else if (f.kind === "int") {
    input = el("input", { type: "number", min: f.min, max: f.max, step: 1 });
  } else {
    input = el("input", { type: "text" });
  }
  input.value = f.saved;
  input.dataset.name = f.name;
  return input;
}

function updateBanner() {
  const pending = fields.some((f) => String(f.saved) !== String(f.running));
  $("#restart-banner").classList.toggle("hidden", !pending);
}

async function loadSettings() {
  try {
    const d = await api("GET", "/api/settings");
    fields = d.fields;
    const box = $("#fields");
    box.replaceChildren();
    fields.forEach((f) => {
      const label = el("label", {}, f.label, buildInput(f));
      if (f.help) label.append(el("span", { className: "muted", textContent: f.help }));
      box.append(label);
    });
    $("#file").textContent = `Saved in ${d.file}`;
    updateBanner();
  } catch (e) {
    setMsg(e.message, "error");
  }
}

$("#save").onclick = async () => {
  const values = {};
  document.querySelectorAll("#fields [data-name]").forEach((n) => { values[n.dataset.name] = n.value; });
  try {
    await api("PUT", "/api/settings", { values });
    setMsg("Saved.", "ok");
    await loadSettings();
  } catch (e) {
    setMsg(e.message, "error");
  }
};

$("#reset").onclick = async () => {
  if (!confirm("Reset every setting to its default?")) return;
  try {
    await api("POST", "/api/settings/reset");
    setMsg("Reset to defaults.", "ok");
    await loadSettings();
  } catch (e) {
    setMsg(e.message, "error");
  }
};

loadSystem();
loadSettings();