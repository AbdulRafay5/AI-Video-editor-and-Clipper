// Stage 4: output format panel. Uses helpers/globals from app.js ($, api, setMsg, currentProject).
let fmtPid = null;
let fmtPreviewUrl = null;

function fmtForm() {
  return { layout: $("#fmt-layout").value, crop_pos: Number($("#fmt-pos").value) / 100 };
}

function fmtToggle() {
  $("#fmt-pos-wrap").classList.toggle("hidden", $("#fmt-layout").value !== "crop");
}

function fmtResetPreview() {
  if (fmtPreviewUrl) URL.revokeObjectURL(fmtPreviewUrl);
  fmtPreviewUrl = null;
  $("#fmt-preview-img").classList.add("hidden");
}

async function loadLayout(pid) {
  try {
    const d = await api("GET", `/api/projects/${pid}/layout`);
    if (!currentProject || currentProject.id !== pid) return;
    $("#fmt-layout").value = d.layout;
    $("#fmt-pos").value = Math.round(d.crop_pos * 100);
    $("#fmt-t").value = Math.min(5, currentProject.duration / 2).toFixed(1);
    fmtToggle();
    setMsg($("#fmt-msg"), d.layout === "original"
      ? "Clips keep the original shape."
      : `New clips will be ${d.out_width}x${d.out_height}.`);
  } catch (e) {
    setMsg($("#fmt-msg"), e.message, "error");
  }
}

async function saveLayout() {
  fmtToggle();
  if (!currentProject) return;
  try {
    const f = fmtForm();
    await api("PUT", `/api/projects/${currentProject.id}/layout`, f);
    setMsg($("#fmt-msg"), f.layout === "original"
      ? "Saved. Clips keep the original shape."
      : "Saved. New clips will use this layout.", "ok");
  } catch (e) {
    setMsg($("#fmt-msg"), e.message, "error");
  }
}

$("#fmt-layout").onchange = saveLayout;
$("#fmt-pos").onchange = saveLayout;

$("#fmt-preview-btn").onclick = async () => {
  if (!currentProject) return;
  const msg = $("#fmt-msg");
  const follow = $("#fmt-layout").value === "follow";
  setMsg(msg, follow ? "Looking for faces and rendering the preview (the first time also downloads a small model)..."
                     : "Rendering preview...");
  try {
    const f = fmtForm();
    const res = await fetch(`/api/projects/${currentProject.id}/layout-preview`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ t: Number($("#fmt-t").value), layout: f.layout, crop_pos: f.crop_pos,
                             captions: $("#fmt-caps").checked }),
    });
    if (!res.ok) {
      let d = {};
      try { d = await res.json(); } catch (_) {}
      throw new Error(d.error || `Preview failed (${res.status})`);
    }
    const events = res.headers.get("X-Caption-Events");
    const layoutNote = res.headers.get("X-Layout-Note");
    const blob = await res.blob();
    fmtResetPreview();
    fmtPreviewUrl = URL.createObjectURL(blob);
    const img = $("#fmt-preview-img");
    img.src = fmtPreviewUrl;
    img.classList.remove("hidden");

    const parts = [];
    if (layoutNote) parts.push(layoutNote);
    if ($("#fmt-caps").checked && events === "0") {
      parts.push("No caption is on screen at this time (or captions aren't generated yet).");
    }
    setMsg(msg, parts.join(" "));
  } catch (e) {
    setMsg(msg, e.message, "error");
  }
};

// Reload whenever a different project is selected.
setInterval(() => {
  if (currentProject && currentProject.id !== fmtPid) {
    fmtPid = currentProject.id;
    fmtResetPreview();
    loadLayout(fmtPid);
  }
}, 700);