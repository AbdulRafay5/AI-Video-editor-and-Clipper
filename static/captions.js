// Stage 2B-1: captions panel. Uses helpers/globals from app.js ($, api, el, setMsg, currentProject).
const CAP_PRESETS = {
  classic: { font: "Arial", size_pct: 6, bold: true, uppercase: false, text_color: "#FFFFFF",
             highlight_color: "#FFFFFF", highlight_word: false, outline_color: "#000000", outline_pct: 0.5 },
  pop:     { font: "Impact", size_pct: 7, bold: false, uppercase: true, text_color: "#FFFFFF",
             highlight_color: "#FFD400", highlight_word: true, outline_color: "#000000", outline_pct: 0.7 },
  clean:   { font: "Segoe UI", size_pct: 5.5, bold: true, uppercase: false, text_color: "#FFFFFF",
             highlight_color: "#4DD2FF", highlight_word: true, outline_color: "#000000", outline_pct: 0.35 },
};

let capPid = null;
let capExists = false;
let capPreviewUrl = null;

const fontsReady = (async () => {
  try {
    const r = await api("GET", "/api/caption-fonts");
    const sel = $("#cap-font");
    sel.replaceChildren();
    r.fonts.forEach((f) => sel.append(el("option", { value: f, textContent: f })));
  } catch (_) {}
})();

function styleToForm(s) {
  $("#cap-font").value = s.font;
  $("#cap-size").value = s.size_pct;
  $("#cap-text-color").value = s.text_color;
  $("#cap-hl-color").value = s.highlight_color;
  $("#cap-outline-color").value = s.outline_color;
  $("#cap-outline").value = s.outline_pct;
  $("#cap-position").value = s.position;
  $("#cap-margin").value = s.margin_pct;
  $("#cap-line-chars").value = s.line_chars;
  $("#cap-bold").checked = s.bold;
  $("#cap-upper").checked = s.uppercase;
  $("#cap-hl-word").checked = s.highlight_word;
}

function styleFromForm() {
  return {
    font: $("#cap-font").value,
    size_pct: Number($("#cap-size").value),
    text_color: $("#cap-text-color").value,
    highlight_color: $("#cap-hl-color").value,
    outline_color: $("#cap-outline-color").value,
    outline_pct: Number($("#cap-outline").value),
    position: $("#cap-position").value,
    margin_pct: Number($("#cap-margin").value),
    line_chars: Number($("#cap-line-chars").value),
    bold: $("#cap-bold").checked,
    uppercase: $("#cap-upper").checked,
    highlight_word: $("#cap-hl-word").checked,
  };
}

function renderCaptionList(caps) {
  const box = $("#cap-list");
  box.replaceChildren();
  caps.forEach((c) => {
    const row = el("div", { className: "cap-row" });
    row.dataset.id = c.id;
    row.append(
      el("input", { type: "number", step: "0.01", min: "0", value: c.start, className: "cap-start" }),
      el("input", { type: "number", step: "0.01", min: "0", value: c.end, className: "cap-end" }),
      el("input", { type: "text", value: c.text, className: "cap-text" }),
      el("button", { className: "secondary", textContent: "X", title: "Remove this caption", onclick: () => row.remove() }),
    );
    box.append(row);
  });
}

function collectEdits() {
  return [...document.querySelectorAll("#cap-list .cap-row")].map((row) => ({
    id: Number(row.dataset.id),
    start: Number(row.querySelector(".cap-start").value),
    end: Number(row.querySelector(".cap-end").value),
    text: row.querySelector(".cap-text").value,
  }));
}

function resetPreview() {
  if (capPreviewUrl) URL.revokeObjectURL(capPreviewUrl);
  capPreviewUrl = null;
  $("#cap-preview-img").classList.add("hidden");
  setMsg($("#cap-preview-msg"), "");
}

async function loadCaptions(pid) {
  await fontsReady;
  let d;
  try {
    d = await api("GET", `/api/projects/${pid}/captions`);
  } catch (e) {
    return setMsg($("#cap-msg"), e.message, "error");
  }
  if (!currentProject || currentProject.id !== pid) return;
  capExists = d.exists;
  styleToForm(d.style);
  $("#cap-editor").classList.toggle("hidden", !d.exists);
  $("#cap-generate").textContent = d.exists ? "Regenerate captions" : "Generate captions";
  if (d.exists) {
    renderCaptionList(d.captions);
    $("#cap-preview-t").value = d.captions.length ? (d.captions[0].start + 0.2).toFixed(1) : 0;
    setMsg($("#cap-msg"), `${d.captions.length} captions.`);
  } else {
    $("#cap-list").replaceChildren();
    setMsg($("#cap-msg"), "No captions yet. Transcribe the video first, then generate captions.");
  }
}

// Reload whenever a different project is selected.
setInterval(() => {
  if (currentProject && currentProject.id !== capPid) {
    capPid = currentProject.id;
    resetPreview();
    loadCaptions(capPid);
  }
}, 700);

$("#cap-generate").onclick = async () => {
  if (!currentProject) return;
  if (capExists && !confirm("Regenerating replaces your caption edits. Continue?")) return;
  try {
    await api("POST", `/api/projects/${currentProject.id}/captions/generate`,
      { max_words: Number($("#cap-max-words").value) });
    resetPreview();
    await loadCaptions(currentProject.id);
  } catch (e) {
    setMsg($("#cap-msg"), e.message, "error");
  }
};

$("#cap-preset").onchange = () => {
  const preset = CAP_PRESETS[$("#cap-preset").value];
  if (!preset) return;
  const cur = styleFromForm();
  const merged = { ...cur, ...preset };
  if (![...$("#cap-font").options].some((o) => o.value === merged.font)) merged.font = cur.font;
  styleToForm(merged);
};

$("#cap-save-style").onclick = async () => {
  try {
    const r = await api("PUT", `/api/projects/${currentProject.id}/caption-style`, { style: styleFromForm() });
    styleToForm(r.style);
    setMsg($("#cap-msg"), "Style saved.", "ok");
  } catch (e) {
    setMsg($("#cap-msg"), e.message, "error");
  }
};

$("#cap-save").onclick = async () => {
  try {
    const r = await api("PUT", `/api/projects/${currentProject.id}/captions`, { captions: collectEdits() });
    renderCaptionList(r.captions);
    setMsg($("#cap-msg"), `Saved ${r.captions.length} captions.`, "ok");
  } catch (e) {
    setMsg($("#cap-msg"), e.message, "error");
  }
};

$("#cap-srt").onclick = () => {
  if (currentProject) location.href = `/api/projects/${currentProject.id}/captions.srt`;
};

$("#cap-preview-btn").onclick = async () => {
  if (!currentProject) return;
  const msg = $("#cap-preview-msg");
  setMsg(msg, "Rendering preview...");
  try {
    const res = await fetch(`/api/projects/${currentProject.id}/caption-preview`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ t: Number($("#cap-preview-t").value), style: styleFromForm(), captions: collectEdits() }),
    });
    if (!res.ok) {
      let d = {};
      try { d = await res.json(); } catch (_) {}
      throw new Error(d.error || `Preview failed (${res.status})`);
    }
    const events = res.headers.get("X-Caption-Events");
    const blob = await res.blob();
    if (capPreviewUrl) URL.revokeObjectURL(capPreviewUrl);
    capPreviewUrl = URL.createObjectURL(blob);
    const img = $("#cap-preview-img");
    img.src = capPreviewUrl;
    img.classList.remove("hidden");
    setMsg(msg, events === "0" ? "No caption is on screen at this time. Pick a time inside a caption." : "", "");
  } catch (e) {
    setMsg(msg, e.message, "error");
  }
};