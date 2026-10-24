// AI highlights panel with approve / dismiss. Uses helpers/globals from app.js
// ($, api, el, setMsg, fmtTime, currentProject).
const HL_CATS = ["funny", "surprising", "emotional", "exciting", "informative"];
let hlPid = null;
let hlSig = "";
let hlShowDismissed = false;

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

async function approveAndClip(h) {
  await api("POST", `/api/highlights/${h.id}/clip`, { burn_captions: $("#burn-captions").checked });
  await api("PUT", `/api/highlights/${h.id}/status`, { status: "approved" });
}

$("#hl-all").onclick = async () => {
  let items;
  try {
    items = (await api("GET", `/api/projects/${currentProject.id}/highlights`)).highlights
      .filter((h) => h.status !== "rejected");
  } catch (e) { return setMsg($("#hl-msg"), e.message, "error"); }
  try {
    for (const h of items) await approveAndClip(h);
    setMsg($("#hl-msg"), `Queued ${items.length} clip(s). See section 3. They render one at a time.`, "ok");
    hlSig = "";
    refreshHighlights();
  } catch (e) { setMsg($("#hl-msg"), e.message, "error"); }
};

async function setStatus(h, status) {
  try {
    await api("PUT", `/api/highlights/${h.id}/status`, { status });
    hlSig = "";
    refreshHighlights();
  } catch (e) { setMsg($("#hl-msg"), e.message, "error"); }
}

function buildHighlight(h, rank) {
  const card = el("div", { className: "clip" });
  if (h.status === "rejected") card.style.opacity = "0.6";
  const head = el("div", { className: "clip-head" });
  const title = el("strong", { textContent: `#${rank}  ${h.category}  |  score ${h.score}` });
  const badge = h.status === "approved" ? el("span", { className: "badge done", textContent: " approved" })
    : h.status === "rejected" ? el("span", { className: "badge", textContent: " dismissed" }) : "";
  const left = el("span");
  left.append(title, " ", badge);
  head.append(left, el("span", { className: "muted",
    textContent: `${fmtTime(h.start_time)} - ${fmtTime(h.end_time)} (${Math.round(h.duration)}s)` }));
  card.append(head);
  if (h.reason) card.append(el("p", { className: "muted", textContent: h.reason }));
  if (h.note) card.append(el("p", { className: "warn", textContent: h.note }));

  const actions = el("div", { className: "actions" });
  if (h.status === "rejected") {
    actions.append(el("button", { className: "secondary", textContent: "Restore", onclick: () => setStatus(h, "suggested") }));
  } else {
    actions.append(
      el("button", {
        textContent: h.status === "approved" ? "Create another clip" : "Approve and create clip",
        onclick: async () => {
          try {
            await approveAndClip(h);
            setMsg($("#hl-msg"), "Clip queued. See section 3.", "ok");
            hlSig = "";
            refreshHighlights();
          } catch (e) { setMsg($("#hl-msg"), e.message, "error"); }
        },
      }),
      el("button", { className: "secondary", textContent: "Dismiss", onclick: () => setStatus(h, "rejected") }),
    );
  }
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
  const usable = d.highlights.filter((h) => h.status !== "rejected");
  $("#hl-all").classList.toggle("hidden", !usable.length);

  let text = "", kind = "";
  if (d.status === "queued") text = "Queued...";
  else if (d.status === "processing") text = `${d.message || "Working"}... The first request loads the model.`;
  else if (d.status === "done") { text = d.message || "Done."; kind = "ok"; }
  else if (d.status === "failed") { text = d.error || "Analysis failed."; kind = "error"; }
  else if (d.status === "cancelled") text = "Cancelled.";
  if (d.status !== "none" && !$("#hl-msg").dataset.pinned) setMsg($("#hl-msg"), text, kind);

  const sig = d.status + ":" + hlShowDismissed + ":" + d.highlights.map((h) => `${h.id}${h.status}`).join(",");
  if (sig === hlSig) return;
  hlSig = sig;
  const box = $("#hl-list");
  box.replaceChildren();
  const dismissed = d.highlights.length - usable.length;
  if (dismissed) {
    box.append(el("button", {
      className: "secondary", style: "margin-bottom:8px",
      textContent: hlShowDismissed ? "Hide dismissed" : `Show dismissed (${dismissed})`,
      onclick: () => { hlShowDismissed = !hlShowDismissed; hlSig = ""; refreshHighlights(); },
    }));
  }
  d.highlights.forEach((h, i) => {
    if (h.status === "rejected" && !hlShowDismissed) return;
    box.append(buildHighlight(h, i + 1));
  });
}

async function refreshHighlights() {
  if (!currentProject) return;
  const pid = currentProject.id;
  if (pid !== hlPid) {
    hlPid = pid;
    hlSig = "";
    hlShowDismissed = false;
    $("#hl-list").replaceChildren();
    setMsg($("#hl-msg"), "");
  }
  try {
    const d = await api("GET", `/api/projects/${pid}/highlights`);
    if (currentProject && currentProject.id === pid) renderHighlights(d);
  } catch (_) {}
}

setInterval(refreshHighlights, 1500);