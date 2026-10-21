// Campaigns page. Self-contained (the main page's helpers aren't loaded here).
const $ = (sel) => document.querySelector(sel);
const LIST_FIELDS = ["platforms", "required_hashtags", "required_caption_text",
  "required_spoken_phrases", "required_visuals", "banned_content", "other_rules"];
let currentId = null;
let pollTimer = null;

function setMsg(node, text, kind) {
  node.textContent = text || "";
  node.className = "msg " + (kind || "");
}
function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  Object.assign(node, props);
  children.forEach((c) => node.append(c));
  return node;
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

// ---------- list ----------
async function loadList() {
  const box = $("#camp-list");
  let d;
  try { d = await api("GET", "/api/campaigns"); } catch (_) { return; }
  box.replaceChildren();
  if (!d.campaigns.length) {
    box.append(el("p", { className: "muted", textContent: "No campaigns yet." }));
    return;
  }
  d.campaigns.forEach((c) => {
    const row = el("div", { className: "project-row" + (c.id === currentId ? " selected" : "") });
    const info = el("div");
    info.append(
      el("strong", { textContent: c.name }),
      el("div", { className: "muted", textContent:
        (c.summary ? c.summary.slice(0, 120) + (c.summary.length > 120 ? "..." : "") : "No summary yet") +
        `  |  used by ${c.projects} project(s)` }),
    );
    const label = c.status === "extracting" ? "reading..." : c.status === "failed" ? "needs attention" : "ready";
    row.append(info, el("span", { className: "badge " + (c.status === "failed" ? "failed" : c.status === "ready" ? "done" : ""), textContent: label }));
    row.onclick = () => openCampaign(c.id);
    box.append(row);
  });
}

// ---------- editor ----------
function setFormDisabled(disabled) {
  document.querySelectorAll("#editor input, #editor textarea, #save-btn, #reextract-btn")
    .forEach((n) => { n.disabled = disabled; });
}

function fillForm(d) {
  $("#f-name").value = d.name;
  $("#f-summary").value = d.rules.summary;
  $("#f-min").value = d.rules.min_seconds;
  $("#f-max").value = d.rules.max_seconds;
  $("#f-pref").value = d.rules.preferred_seconds;
  LIST_FIELDS.forEach((f) => { $(`#f-${f}`).value = d.rules[f].join("\n"); });
  $("#source-summary").textContent = `Source text (${d.source_name}, ${d.source_chars.toLocaleString()} characters)`;
  $("#source-text").textContent = d.source_preview + (d.source_chars > 3000 ? "\n...(shortened)" : "");

  const busy = d.status === "extracting";
  setFormDisabled(busy);
  if (busy) setMsg($("#editor-msg"), `The AI is reading the document... ${d.message || ""}`);
  else if (d.status === "failed") setMsg($("#editor-msg"), d.error || "Reading failed. Fill in the rules by hand.", "error");
  else setMsg($("#editor-msg"), d.message || "", "ok");
}

function collectRules() {
  const rules = {
    summary: $("#f-summary").value,
    min_seconds: Number($("#f-min").value) || 0,
    max_seconds: Number($("#f-max").value) || 0,
    preferred_seconds: Number($("#f-pref").value) || 0,
  };
  LIST_FIELDS.forEach((f) => { rules[f] = $(`#f-${f}`).value.split("\n"); });
  return rules;
}

async function openCampaign(id) {
  currentId = id;
  clearInterval(pollTimer);
  let d;
  try { d = await api("GET", `/api/campaigns/${id}`); }
  catch (e) { return setMsg($("#editor-msg"), e.message, "error"); }
  if (currentId !== id) return;
  $("#editor").classList.remove("hidden");
  fillForm(d);
  loadList();
  if (d.status === "extracting") pollTimer = setInterval(() => pollCampaign(id), 1500);
}

async function pollCampaign(id) {
  if (currentId !== id) return clearInterval(pollTimer);
  try {
    const d = await api("GET", `/api/campaigns/${id}`);
    if (d.status === "extracting") {
      setMsg($("#editor-msg"), `The AI is reading the document... ${d.message || ""}`);
    } else {
      openCampaign(id);   // finished: refresh the form with the result
    }
  } catch (_) {}
}

$("#save-btn").onclick = async () => {
  try {
    await api("PUT", `/api/campaigns/${currentId}`, { name: $("#f-name").value, rules: collectRules() });
    await openCampaign(currentId);
    setMsg($("#editor-msg"), "Saved.", "ok");
  } catch (e) {
    setMsg($("#editor-msg"), e.message, "error");
  }
};

$("#reextract-btn").onclick = async () => {
  if (!confirm("The AI will read the document again and replace the rules above. Continue?")) return;
  try {
    await api("POST", `/api/campaigns/${currentId}/reextract`);
    openCampaign(currentId);
  } catch (e) {
    setMsg($("#editor-msg"), e.message, "error");
  }
};

$("#delete-btn").onclick = async () => {
  if (!confirm("Delete this campaign? Projects using it will go back to no campaign.")) return;
  try {
    await api("DELETE", `/api/campaigns/${currentId}`);
    currentId = null;
    clearInterval(pollTimer);
    $("#editor").classList.add("hidden");
    loadList();
  } catch (e) {
    setMsg($("#editor-msg"), e.message, "error");
  }
};

// ---------- create ----------
$("#new-btn").onclick = async () => {
  const msg = $("#new-msg");
  const fd = new FormData();
  fd.append("name", $("#new-name").value);
  const file = $("#new-file").files[0];
  if (file) fd.append("file", file);
  else fd.append("text", $("#new-text").value);
  $("#new-btn").disabled = true;
  setMsg(msg, "Uploading...");
  try {
    const res = await fetch("/api/campaigns", { method: "POST", body: fd });
    let d = {};
    try { d = await res.json(); } catch (_) {}
    if (!res.ok) throw new Error(d.error || `Request failed (${res.status})`);
    $("#new-name").value = "";
    $("#new-file").value = "";
    $("#new-text").value = "";
    setMsg(msg, "Created. The AI is reading it now.", "ok");
    await loadList();
    openCampaign(d.id);
  } catch (e) {
    setMsg(msg, e.message, "error");
  } finally {
    $("#new-btn").disabled = false;
  }
};

loadList();