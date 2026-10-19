// Stage 3B: AI highlights panel. Uses helpers/globals from app.js ($, api, el, setMsg, fmtTime, currentProject).
const HL_CATS = ["funny", "surprising", "emotional", "exciting", "informative"];
let hlPid = null;
let hlSig = "";

function hlSettings() {
  const v = $("#hl-duration").value;
  const raw = v === "custom" ? Number($("#hl-custom").value) : Number(v);
  return {
    mode: v === "variable" ? "variable" : "fixed",
    duration: Number.isFinite(raw) ? raw : 30,
    min_len: Number($("#hl-min").value),
    max_len: Number($("#hl-max").value),
    count: Number($("#hl-count").value),
    categories: HL_CATS.filter((c) => $(`#hl-cat-${c}`).checked),
    focus: $("#hl-focus").value,
  };
}

$("#hl-duration").onchange = () => {
  const v = $("#hl-duration").value;
  $("#hl-custom-wrap").classList.toggle("hidden", v !== "custom");
  $("#hl-minmax").classList.toggle("hidden", v !== "variable");
};

$("#hl-run").onclick = async () => {
  if (!currentProject) return;
  try {
    await api("POST", `/api/projects/${currentProject.id}/analyze`, hlSettings());
    hlSig = "";
    refreshHighlights();
  } catch (e) {
    setMsg($("#hl-msg"), e.message, "error");
  }
};

$("#hl-cancel").onclick = async () => {
  if (!currentProject) return;
  try { await api("POST", `/api/projects/${currentProject.id}/analyze/cancel`); } catch (_) {}
  refreshHighlights();
};

async function makeClipFromHighlight(h) {
  return api("POST", `/api/highlights/${h.id}/clip`, { burn_captions: $("#burn-captions").checked });
}

$("#hl-all").onclick = async () => {
  let items;
  try { items = (await api("GET", `/api/projects/${currentProject.id}/highlights`)).highlights; }
  catch (e) { return setMsg($("#hl-msg"), e.message, "error"); }
  try {
    for (const h of items) await makeClipFromHighlight(h);
    setMsg($("#hl-msg"), `Queued ${items.length} clip(s). See section 3. They render one at a time.`, "ok");
  } catch (e) { setMsg($("#hl-msg"), e.message, "error"); }
};

function buildHighlight(h, rank) {
  const card = el("div", { className: "clip" });
  const head = el("div", { className: "clip-head" });
  head.append(
    el("strong", { textContent: `#${rank}  ${h.category}  |  score ${h.score}` }),
    el("span", { className: "muted", textContent: `${fmtTime(h.start_time)} - ${fmtTime(h.end_time)} (${Math.round(h.duration)}s)` }),
  );
  card.append(head);
  if (h.reason) card.append(el("p", { className: "muted", textContent: h.reason }));
  if (h.note) card.append(el("p", { className: "warn", textContent: h.note }));
  const actions = el("div", { className: "actions" });
  actions.append(el("button", {
    textContent: "Create clip",
    onclick: async () => {
      try {
        await makeClipFromHighlight(h);
        setMsg($("#hl-msg"), "Clip queued. See section 3.", "ok");
      } catch (e) { setMsg($("#hl-msg"), e.message, "error"); }
    },
  }));
  card.append(actions);
  return card;
}

function renderHighlights(d) {
  const active = d.status === "queued" || d.status === "processing";
  $("#hl-run").disabled = active;
  $("#hl-cancel").classList.toggle("hidden", !active);
  const bar = $("#hl-progress");
  bar.classList.toggle("hidden", !active);
  bar.value = d.progress || 0;
  $("#hl-all").classList.toggle("hidden", !d.highlights.length);

  let text = "", kind = "";
  if (d.status === "queued") text = "Queued...";
  else if (d.status === "processing") text = `${d.message || "Working"}... The first request loads the model.`;
  else if (d.status === "done") { text = d.message || "Done."; kind = "ok"; }
  else if (d.status === "failed") { text = d.error || "Analysis failed."; kind = "error"; }
  else if (d.status === "cancelled") text = "Cancelled.";
  if (d.status !== "none" && !$("#hl-msg").dataset.pinned) setMsg($("#hl-msg"), text, kind);

  const sig = d.status + ":" + d.highlights.map((h) => h.id).join(",");
  if (sig === hlSig) return;
  hlSig = sig;
  const box = $("#hl-list");
  box.replaceChildren();
  d.highlights.forEach((h, i) => box.append(buildHighlight(h, i + 1)));
}

async function refreshHighlights() {
  if (!currentProject) return;
  const pid = currentProject.id;
  if (pid !== hlPid) {
    hlPid = pid;
    hlSig = "";
    $("#hl-list").replaceChildren();
    setMsg($("#hl-msg"), "");
  }
  try {
    const d = await api("GET", `/api/projects/${pid}/highlights`);
    if (currentProject && currentProject.id === pid) renderHighlights(d);
  } catch (_) {}
}

setInterval(refreshHighlights, 1500);