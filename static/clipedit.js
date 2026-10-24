// Clip editor: rename, trim, change layout/captions, swap in another suggestion, compare with the
// original footage. Uses helpers/globals from app.js ($, api, el, setMsg, fmtTime, currentProject,
// lastSig, refreshClips).
const EDIT_LAYOUTS = [
  ["original", "Original shape"],
  ["blur", "Vertical - blurred background"],
  ["crop", "Vertical - crop to fill"],
  ["follow", "Vertical - auto-follow faces (beta)"],
  ["fit", "Vertical - fit with black bars"],
];

(function addStyles() {
  const css = `
    .clip-edit { margin-top:10px; padding:10px; border:1px dashed var(--border); border-radius:8px; }
    .clip-edit .form-grid { margin-bottom:8px; }
    .nudge { display:flex; gap:4px; flex-wrap:wrap; margin-top:4px; }
    .nudge button { padding:3px 8px; font-size:.8rem; }
    .compare { display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-top:10px; }
    .compare video { max-width:100%; }
    @media (max-width: 600px) { .compare { grid-template-columns:1fr; } }
  `;
  document.head.append(el("style", { textContent: css }));
})();

function labelled(text, control) {
  return el("label", {}, text, control);
}

function nudgeRow(input, onChange) {
  const row = el("div", { className: "nudge" });
  [-1, -0.5, 0.5, 1].forEach((d) => row.append(el("button", {
    type: "button", className: "secondary", textContent: (d > 0 ? "+" : "") + d + "s",
    onclick: () => { input.value = Math.max(0, Number(input.value) + d).toFixed(2); onChange(); },
  })));
  return row;
}

function buildEditor(card, clip, highlights) {
  const panel = el("div", { className: "clip-edit" });
  const msg = el("p", { className: "msg" });

  const name = el("input", { type: "text", value: clip.name, maxLength: 120 });
  const start = el("input", { type: "number", step: "0.1", min: "0", value: clip.start_time.toFixed(2) });
  const end = el("input", { type: "number", step: "0.1", min: "0", value: clip.end_time.toFixed(2) });
  const lengthLabel = el("span", { className: "muted" });
  const updateLength = () => {
    lengthLabel.textContent = `Length: ${(Number(end.value) - Number(start.value)).toFixed(1)}s`;
  };
  start.oninput = updateLength;
  end.oninput = updateLength;
  updateLength();

  const startBox = labelled("Start (seconds)", start);
  startBox.append(nudgeRow(start, updateLength));
  const endBox = labelled("End (seconds)", end);
  endBox.append(nudgeRow(end, updateLength));

  const layoutSel = el("select");
  EDIT_LAYOUTS.forEach(([v, t]) => layoutSel.append(el("option", { value: v, textContent: t })));
  layoutSel.value = clip.layout || "original";
  const pos = el("input", { type: "range", min: 0, max: 100, value: Math.round((clip.crop_pos ?? 0.5) * 100) });
  const posBox = labelled("Crop position (left to right)", pos);
  const togglePos = () => posBox.classList.toggle("hidden", layoutSel.value !== "crop");
  layoutSel.onchange = togglePos;
  togglePos();

  const suggestions = el("select");
  suggestions.append(el("option", { value: "", textContent: "Keep my times" }));
  highlights.filter((h) => h.status !== "rejected").forEach((h) => suggestions.append(el("option", {
    value: String(h.id),
    textContent: `${h.category} ${fmtTime(h.start_time)}-${fmtTime(h.end_time)} (score ${h.score})`,
  })));
  suggestions.onchange = () => {
    const h = highlights.find((x) => String(x.id) === suggestions.value);
    if (!h) return;
    start.value = h.start_time.toFixed(2);
    end.value = h.end_time.toFixed(2);
    updateLength();
  };

  const burn = el("input", { type: "checkbox", checked: Boolean(clip.burn_captions) });
  const burnLabel = el("label", { className: "check" }, burn, "Burn captions into this clip");

  const grid = el("div", { className: "form-grid" });
  grid.append(
    labelled("Name", name),
    startBox, endBox,
    labelled("Use a different AI suggestion", suggestions),
    labelled("Layout", layoutSel), posBox,
  );
  panel.append(grid, el("div", {}, lengthLabel), burnLabel);
  panel.append(el("p", { className: "muted", textContent:
    "Changing the times, layout or captions re-renders the clip. Renaming does not." }));

  const compareBox = el("div");
  const actions = el("div", { className: "actions" });

  const save = el("button", { textContent: "Save", onclick: async () => {
    const s = Number(start.value), e2 = Number(end.value);
    if (!(e2 > s)) return setMsg(msg, "The end must be after the start.", "error");
    // let go of the video file first, so Windows can replace it
    card.querySelectorAll("video").forEach((v) => { v.pause(); v.removeAttribute("src"); v.load(); });
    await new Promise((r) => setTimeout(r, 400));
    try {
      const r = await api("PUT", `/api/clips/${clip.id}`, {
        name: name.value, start: s, end: e2, layout: layoutSel.value,
        crop_pos: Number(pos.value) / 100, burn_captions: burn.checked,
      });
      setMsg($("#clip-msg"), r.rendering ? "Saved. The clip is re-rendering..." : "Saved.", "ok");
      lastSig = "";
      refreshClips();
    } catch (err) {
      setMsg(msg, err.message, "error");
    }
  } });

  const compare = el("button", { className: "secondary", textContent: "Compare with original footage", onclick: () => {
    compareBox.replaceChildren();
    if (clip.status !== "done") {
      return compareBox.append(el("p", { className: "msg error", textContent: "Only finished clips can be compared." }));
    }
    const info = el("p", { className: "muted", textContent: "Preparing the original footage preview (a few seconds)..." });
    const original = el("video", { controls: true, preload: "auto",
      src: `/api/clips/${clip.id}/original-preview?v=${clip.start_time}-${clip.end_time}` });
    const final = el("video", { controls: true, preload: "metadata", src: `/api/clips/${clip.id}/file` });
    original.onloadeddata = () => { info.textContent = "Left: original footage (360p preview). Right: your final clip."; };
    original.onerror = () => { info.textContent = "Could not prepare the original footage preview."; };
    const grid2 = el("div", { className: "compare" });
    grid2.append(original, final);
    const controls = el("div", { className: "actions" });
    controls.append(
      el("button", { className: "secondary", textContent: "Play both", onclick: () => { original.play(); final.play(); } }),
      el("button", { className: "secondary", textContent: "Pause both", onclick: () => { original.pause(); final.pause(); } }),
      el("button", { className: "secondary", textContent: "Restart", onclick: () => { original.currentTime = 0; final.currentTime = 0; } }),
    );
    compareBox.append(info, grid2, controls);
  } });

  const close = el("button", { className: "secondary", textContent: "Close", onclick: () => panel.remove() });
  actions.append(save, compare, close);
  panel.append(actions, msg, compareBox);
  return panel;
}

async function toggleEditor(card, id) {
  const existing = card.querySelector(".clip-edit");
  if (existing) return existing.remove();
  try {
    const pid = currentProject.id;
    const [clips, hl] = await Promise.all([
      api("GET", `/api/projects/${pid}/clips`),
      api("GET", `/api/projects/${pid}/highlights`),
    ]);
    const clip = clips.find((c) => c.id === id);
    if (clip) card.append(buildEditor(card, clip, hl.highlights));
  } catch (e) {
    setMsg($("#clip-msg"), e.message, "error");
  }
}

function addEditButtons() {
  document.querySelectorAll("#clips .clip").forEach((card) => {
    if (card.querySelector(".edit-btn") || card.querySelector("progress")) return;   // not while rendering
    const actions = card.querySelector(".actions");
    const id = Number(card.id.replace("clip-", ""));
    if (!actions || !id) return;
    actions.prepend(el("button", { className: "secondary edit-btn", textContent: "Edit",
      onclick: () => toggleEditor(card, id) }));
  });
}

// app.js rebuilds the clip cards whenever a status changes: add the buttons back each time.
new MutationObserver(addEditButtons).observe($("#clips"), { childList: true });