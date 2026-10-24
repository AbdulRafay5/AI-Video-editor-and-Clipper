// Dashboard page (self-contained: the main page's helpers aren't loaded here).
const $ = (sel) => document.querySelector(sel);

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  Object.assign(node, props);
  children.forEach((c) => node.append(c));
  return node;
}
function setMsg(node, text, kind) {
  node.textContent = text || "";
  node.className = "msg " + (kind || "");
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

const pad = (n) => String(n).padStart(2, "0");
function fmtDur(sec) {
  sec = Math.round(sec || 0);
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}
function fmtSize(bytes) {
  if (bytes >= 1e9) return (bytes / 1e9).toFixed(2) + " GB";
  if (bytes >= 1e6) return (bytes / 1e6).toFixed(1) + " MB";
  return Math.round(bytes / 1e3) + " KB";
}
function cell(content) {
  return el("td", {}, content);
}

function statBox(label, value) {
  return el("div", { className: "stat" }, el("strong", { textContent: String(value) }),
    el("span", { className: "muted", textContent: label }));
}

async function load() {
  let d;
  try { d = await api("GET", "/api/dashboard"); }
  catch (e) { return setMsg($("#msg"), e.message, "error"); }
  setMsg($("#msg"), "");

  const stats = $("#stats");
  stats.replaceChildren(
    statBox("Projects", d.totals.projects),
    statBox("Finished clips", d.totals.clips_done),
    statBox("Failed clips", d.totals.clips_failed),
    statBox("Campaigns", d.totals.campaigns),
    statBox("Jobs running", d.running),
  );

  const rows = $("#projects");
  rows.replaceChildren();
  if (!d.projects.length) rows.append(el("tr", {}, el("td", { colSpan: 8, className: "muted", textContent: "No projects yet." })));
  d.projects.forEach((p) => {
    const clips = p.clips_total
      ? `${p.clips_done} done` + (p.clips_failed ? `, ${p.clips_failed} failed` : "") + ` of ${p.clips_total}`
      : "none";
    rows.append(el("tr", {},
      cell(el("strong", { textContent: p.original_filename })),
      cell(fmtDur(p.duration)),
      cell(fmtSize(p.size_bytes)),
      cell(clips),
      cell(p.transcript || "not yet"),
      cell(p.has_captions ? "yes" : "no"),
      cell(p.campaign || "-"),
      cell(el("a", { href: `/#project=${p.id}`, textContent: "Open" })),
    ));
  });

  const recent = $("#recent");
  recent.replaceChildren();
  if (!d.recent.length) recent.append(el("tr", {}, el("td", { colSpan: 4, className: "muted", textContent: "No clips yet." })));
  d.recent.forEach((c) => {
    recent.append(el("tr", {},
      cell(c.name), cell(el("a", { href: `/#project=${c.project_id}`, textContent: c.project })),
      cell(c.status), cell(c.created_at || ""),
    ));
  });

  $("#free").textContent = `${fmtSize(d.free_bytes)} free on this drive.`;
  const disk = $("#disk");
  disk.replaceChildren();
  d.disk.forEach((item) => {
    const box = el("input", { type: "checkbox" });
    box.dataset.key = item.key;
    box.disabled = !item.cleanable;
    disk.append(el("tr", {},
      cell(item.cleanable ? box : ""), cell(item.label), cell(fmtSize(item.bytes))));
  });
}

$("#refresh").onclick = load;

$("#cleanup").onclick = async () => {
  const targets = [...document.querySelectorAll("#disk input:checked")].map((b) => b.dataset.key);
  const msg = $("#cleanup-msg");
  if (!targets.length) return setMsg(msg, "Tick what to remove first.", "error");
  if (!confirm("Remove the ticked temporary files?")) return;
  try {
    const r = await api("POST", "/api/cleanup", { targets });
    setMsg(msg, `Freed ${fmtSize(r.freed)}.`, "ok");
    load();
  } catch (e) {
    setMsg(msg, e.message, "error");
  }
};

load();