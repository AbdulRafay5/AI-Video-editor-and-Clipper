// Campaign picker for a project, plus a compliance checklist under each finished clip.
// Uses helpers/globals from app.js ($, api, el, setMsg, currentProject).
let pcPid = null;
let pcHasCampaign = false;
const pcReports = new Map();   // clip id -> report (or null)

const COMP_TAG = { pass: "Verified", review: "Check by hand", fail: "Does not comply" };
const COMP_HEAD = {
  pass: "All automatic checks passed",
  review: "Needs a manual check",
  fail: "Does not meet the campaign rules",
  none: "This campaign has no rules to check",
};

(function addStyles() {
  const css = `
    .compliance { margin-top:10px; padding:10px; border:1px solid var(--border); border-radius:8px; }
    .comp-row { display:flex; gap:8px; align-items:flex-start; margin:6px 0; font-size:.9rem; }
    .comp-tag { flex:0 0 auto; font-size:.72rem; padding:2px 8px; border-radius:999px; color:#fff; white-space:nowrap; }
    .comp-tag.pass { background:var(--ok); }
    .comp-tag.review { background:#b7791f; }
    .comp-tag.fail { background:var(--danger); }
    .comp-post { width:100%; margin-top:6px; padding:8px; border:1px solid var(--border); border-radius:6px;
                 background:var(--bg); color:var(--text); font:inherit; resize:vertical; }
  `;
  document.head.append(el("style", { textContent: css }));
})();

// ---------- campaign picker ----------

function pcDescribe(rules) {
  if (!rules) return "No campaign: highlights use only the settings in section 6.";
  const bits = [];
  if (rules.min_seconds || rules.max_seconds) bits.push(`length ${rules.min_seconds || "?"}-${rules.max_seconds || "?"}s`);
  else if (rules.preferred_seconds) bits.push(`length about ${rules.preferred_seconds}s`);
  if (rules.required_hashtags.length) bits.push("hashtags " + rules.required_hashtags.join(" "));
  if (rules.required_visuals.length) bits.push("must show " + rules.required_visuals.join(", "));
  if (rules.required_spoken_phrases.length) bits.push("must say " + rules.required_spoken_phrases.join(", "));
  return bits.length ? "Rules: " + bits.join(" | ") : "This campaign has no rules yet. Open the Campaigns page and review it.";
}

async function loadProjectCampaign(pid) {
  try {
    const [list, cur] = await Promise.all([
      api("GET", "/api/campaigns"),
      api("GET", `/api/projects/${pid}/campaign`),
    ]);
    if (!currentProject || currentProject.id !== pid) return;
    const sel = $("#pc-select");
    sel.replaceChildren(el("option", { value: "", textContent: "No campaign" }));
    list.campaigns.forEach((c) => sel.append(el("option", {
      value: String(c.id),
      textContent: c.name + (c.status === "extracting" ? " (still reading...)" : ""),
    })));
    sel.value = cur.campaign_id ? String(cur.campaign_id) : "";
    pcHasCampaign = Boolean(cur.campaign_id);
    setMsg($("#pc-msg"), pcDescribe(cur.rules));
  } catch (e) {
    setMsg($("#pc-msg"), e.message, "error");
  }
}

function refreshCompliancePanels() {
  pcReports.clear();
  document.querySelectorAll("#clips .compliance").forEach((n) => n.remove());
  attachCompliance();
}

$("#pc-select").onchange = async () => {
  if (!currentProject) return;
  try {
    await api("PUT", `/api/projects/${currentProject.id}/campaign`,
      { campaign_id: $("#pc-select").value || null });
    await loadProjectCampaign(currentProject.id);
    refreshCompliancePanels();
  } catch (e) {
    setMsg($("#pc-msg"), e.message, "error");
  }
};

// Refresh the list when the dropdown is opened, so new campaigns appear.
$("#pc-select").onfocus = () => { if (currentProject) loadProjectCampaign(currentProject.id); };

setInterval(() => {
  if (currentProject && currentProject.id !== pcPid) {
    pcPid = currentProject.id;
    pcReports.clear();
    loadProjectCampaign(pcPid);
  }
}, 700);

// ---------- compliance panels under finished clips ----------

function buildReport(box, id, report) {
  box.replaceChildren();
  const head = el("div", { className: "row-between" });
  head.append(
    el("strong", { textContent: `Campaign check: ${COMP_HEAD[report.overall] || report.overall}` }),
    el("button", { className: "secondary", textContent: "Re-check", onclick: () => runCheck(box, id) }),
  );
  box.append(head);
  box.append(el("p", { className: "muted", textContent:
    `Against "${report.campaign}". The app only verifies what it can; everything else is flagged for you to check.` }));

  report.checks.forEach((c) => {
    const row = el("div", { className: "comp-row" });
    row.append(el("span", { className: `comp-tag ${c.status}`, textContent: COMP_TAG[c.status] || c.status }));
    const body = el("div");
    body.append(el("strong", { textContent: c.rule }), el("div", { className: "muted", textContent: c.detail }));
    row.append(body);
    box.append(row);
  });

  if (report.post_text) {
    box.append(el("strong", { textContent: "Suggested post text" }));
    const ta = el("textarea", { className: "comp-post", readOnly: true, rows: 3, value: report.post_text });
    const copy = el("button", { className: "secondary", textContent: "Copy post text" });
    copy.onclick = async () => {
      try { await navigator.clipboard.writeText(report.post_text); }
      catch (_) { ta.select(); document.execCommand("copy"); }
      copy.textContent = "Copied";
      setTimeout(() => { copy.textContent = "Copy post text"; }, 1500);
    };
    box.append(ta, el("div", { className: "actions" }, copy));
  }
}

async function runCheck(box, id) {
  box.replaceChildren(el("p", { className: "muted", textContent: "Checking the clip..." }));
  try {
    const r = await api("POST", `/api/clips/${id}/compliance`);
    pcReports.set(id, r.report);
    renderCompliance(box, id);
  } catch (e) {
    box.replaceChildren(el("p", { className: "msg error", textContent: e.message }));
  }
}

async function renderCompliance(box, id) {
  let report;
  if (pcReports.has(id)) {
    report = pcReports.get(id);
  } else {
    try { report = (await api("GET", `/api/clips/${id}/compliance`)).report; } catch (_) { return; }
    pcReports.set(id, report);
  }
  if (report) return buildReport(box, id, report);
  if (!pcHasCampaign) return box.remove();
  box.replaceChildren(el("button", {
    className: "secondary", textContent: "Check against campaign", onclick: () => runCheck(box, id),
  }));
}

function attachCompliance() {
  document.querySelectorAll("#clips .clip").forEach((card) => {
    if (card.querySelector(".compliance") || !card.querySelector("video")) return;   // finished clips only
    const id = Number(card.id.replace("clip-", ""));
    if (!id) return;
    const box = el("div", { className: "compliance" });
    card.append(box);
    renderCompliance(box, id);
  });
}

// app.js rebuilds the clip cards whenever a status changes: add the panels back each time.
new MutationObserver(attachCompliance).observe($("#clips"), { childList: true });