// Campaign picker for a project. Uses helpers/globals from app.js ($, api, el, setMsg, currentProject).
let pcPid = null;

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
    setMsg($("#pc-msg"), pcDescribe(cur.rules));
  } catch (e) {
    setMsg($("#pc-msg"), e.message, "error");
  }
}

$("#pc-select").onchange = async () => {
  if (!currentProject) return;
  try {
    await api("PUT", `/api/projects/${currentProject.id}/campaign`,
      { campaign_id: $("#pc-select").value || null });
    loadProjectCampaign(currentProject.id);
  } catch (e) {
    setMsg($("#pc-msg"), e.message, "error");
  }
};

// Refresh the list when the dropdown is opened, so new campaigns appear.
$("#pc-select").onfocus = () => { if (currentProject) loadProjectCampaign(currentProject.id); };

setInterval(() => {
  if (currentProject && currentProject.id !== pcPid) {
    pcPid = currentProject.id;
    loadProjectCampaign(pcPid);
  }
}, 700);