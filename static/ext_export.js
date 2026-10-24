// Export tools: tick finished clips, download them as one zip, open the output folder.
// Loaded automatically (static/ext_*.js). Uses helpers/globals from app.js.
const exportSelected = new Set();
let exportPid = null;

const exportBar = el("div", { className: "actions", id: "export-bar" });
const exportMsg = el("p", { className: "msg", id: "export-msg" });

function finishedCardIds() {
  return [...document.querySelectorAll("#clips .clip")]
    .filter((card) => card.querySelector("video"))
    .map((card) => Number(card.id.replace("clip-", "")));
}

function syncCheckboxes() {
  document.querySelectorAll("#clips .export-check input").forEach((box) => {
    box.checked = exportSelected.has(Number(box.dataset.id));
  });
}

exportBar.append(
  el("button", { className: "secondary", textContent: "Select all finished", onclick: () => {
    finishedCardIds().forEach((id) => exportSelected.add(id));
    syncCheckboxes();
  } }),
  el("button", { className: "secondary", textContent: "Clear selection", onclick: () => {
    exportSelected.clear();
    syncCheckboxes();
  } }),
  el("button", { textContent: "Download selected as zip", onclick: () => {
    if (!currentProject) return;
    const ids = [...exportSelected].filter((id) => finishedCardIds().includes(id));
    if (!ids.length) return setMsg(exportMsg, "Tick the clips you want first, or press Select all finished.", "error");
    setMsg(exportMsg, "Preparing the zip... the download starts when it is ready.", "");
    location.href = `/api/projects/${currentProject.id}/export.zip?ids=${ids.join(",")}`;
  } }),
  el("button", { className: "secondary", textContent: "Open output folder", onclick: async () => {
    if (!currentProject) return;
    try {
      const r = await api("POST", `/api/projects/${currentProject.id}/open-folder`);
      setMsg(exportMsg, `Opened ${r.path}`, "ok");
    } catch (e) {
      setMsg(exportMsg, e.message, "error");
    }
  } }),
);
$("#clips").before(exportBar, exportMsg);

function addExportChecks() {
  document.querySelectorAll("#clips .clip").forEach((card) => {
    if (card.querySelector(".export-check") || !card.querySelector("video")) return;   // finished clips only
    const id = Number(card.id.replace("clip-", ""));
    const head = card.querySelector(".clip-head");
    if (!id || !head) return;
    const box = el("input", { type: "checkbox" });
    box.dataset.id = String(id);
    box.checked = exportSelected.has(id);
    box.onchange = () => { box.checked ? exportSelected.add(id) : exportSelected.delete(id); };
    head.prepend(el("label", { className: "check export-check" }, box, "Export"));
  });
}

// app.js rebuilds the clip cards whenever a status changes: add the checkboxes back each time.
new MutationObserver(addExportChecks).observe($("#clips"), { childList: true });

setInterval(() => {
  if (currentProject && currentProject.id !== exportPid) {
    exportPid = currentProject.id;
    exportSelected.clear();
    setMsg(exportMsg, "");
  }
}, 700);