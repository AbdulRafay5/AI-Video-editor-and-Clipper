// Stage 2A transcript panel. Uses helpers/globals from app.js ($, api, el, fmtTime, setMsg, currentProject).
let transcriptPid = null;
let transcriptKey = "";

function resetTranscriptUI() {
  $("#transcript-list").replaceChildren();
  $("#transcript-progress").classList.add("hidden");
}

function renderTranscript(t) {
  const box = $("#transcript-list");
  box.replaceChildren();
  t.segments.forEach((s) => {
    const row = el("div", { className: "seg" });
    row.append(el("span", { className: "seg-time", textContent: fmtTime(s.start) }), el("span", { textContent: s.text }));
    box.append(row);
  });
}

function updateTranscriptControls(t) {
  const active = t.status === "queued" || t.status === "processing";
  $("#transcribe-btn").disabled = active;
  $("#transcribe-btn").textContent = t.status === "done" ? "Re-transcribe" : "Transcribe";
  $("#transcribe-cancel").classList.toggle("hidden", !active);
  const bar = $("#transcript-progress");
  bar.classList.toggle("hidden", !active);
  bar.value = t.progress || 0;

  const info = [t.model, t.device].filter(Boolean).join(", ");
  let text = "Not transcribed yet.";
  let kind = "";
  if (t.status === "queued") text = "Queued...";
  else if (t.status === "processing") {
    text = `Transcribing (${info || "starting"})... ${Math.round((t.progress || 0) * 100)}%. The first run downloads the model, which can take a few minutes.`;
  } else if (t.status === "done") { text = `Done. Language: ${t.language}. ${info}.`; kind = "ok"; }
  else if (t.status === "failed") { text = t.error || "Transcription failed."; kind = "error"; }
  else if (t.status === "cancelled") text = "Cancelled.";
  setMsg($("#transcript-msg"), text, kind);
}

async function refreshTranscript() {
  if (!currentProject) return;
  const pid = currentProject.id;
  if (pid !== transcriptPid) { transcriptPid = pid; transcriptKey = ""; resetTranscriptUI(); }
  let t;
  try { t = await api("GET", `/api/projects/${pid}/transcript?light=1`); } catch (_) { return; }
  if (!currentProject || currentProject.id !== pid) return;
  updateTranscriptControls(t);
  const key = `${pid}:${t.status}`;
  if (key !== transcriptKey) {
    transcriptKey = key;
    if (t.status === "done") {
      try { renderTranscript(await api("GET", `/api/projects/${pid}/transcript`)); } catch (_) {}
    } else {
      resetTranscriptUI();
    }
  }
}

$("#transcribe-btn").onclick = async () => {
  if (!currentProject) return;
  if ($("#transcribe-btn").textContent === "Re-transcribe" && !confirm("Replace the existing transcript?")) return;
  try {
    await api("POST", `/api/projects/${currentProject.id}/transcribe`);
    transcriptKey = "";
    refreshTranscript();
  } catch (e) { setMsg($("#transcript-msg"), e.message, "error"); }
};

$("#transcribe-cancel").onclick = async () => {
  if (!currentProject) return;
  try { await api("POST", `/api/projects/${currentProject.id}/transcribe/cancel`); } catch (_) {}
  refreshTranscript();
};

setInterval(refreshTranscript, 1500);