const $ = (sel) => document.querySelector(sel);
let currentProject = null;
let pollTimer = null;
let lastSig = "";

// ---------- helpers ----------
const pad = (n) => String(n).padStart(2, "0");
function fmtTime(sec) {
  sec = Math.round(sec);
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}
function fmtSize(bytes) {
  return bytes >= 1e9 ? (bytes / 1e9).toFixed(2) + " GB" : (bytes / 1e6).toFixed(1) + " MB";
}
function parseTime(str) {
  const parts = String(str).trim().split(":").map(Number);
  if (parts.some((n) => Number.isNaN(n) || n < 0)) return null;
  return parts.reduce((acc, n) => acc * 60 + n, 0);
}
function setMsg(el, text, kind) {
  el.textContent = text;
  el.className = "msg " + (kind || "");
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
function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  Object.assign(node, props);
  children.forEach((c) => node.append(c));
  return node;
}

// ---------- status ----------
api("GET", "/api/status").then((s) => { $("#encoder-note").textContent = s.message; })
  .catch(() => {});

// ---------- upload ----------
function uploadFile(file) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/projects");
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) $("#upload-progress").value = e.loaded / e.total;
    };
    xhr.onload = () => {
      let data = {};
      try { data = JSON.parse(xhr.responseText); } catch (_) {}
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else reject(new Error(data.error || `Upload failed (${xhr.status})`));
    };
    xhr.onerror = () => reject(new Error("Network error during upload."));
    const fd = new FormData();
    fd.append("video", file);
    xhr.send(fd);
  });
}

$("#upload-btn").onclick = async () => {
  const file = $("#file").files[0];
  if (!file) return setMsg($("#upload-msg"), "Choose a video file first.", "error");
  $("#upload-btn").disabled = true;
  $("#upload-progress").classList.remove("hidden");
  $("#upload-progress").value = 0;
  setMsg($("#upload-msg"), "Uploading... large files can take a while.");
  try {
    const project = await uploadFile(file);
    setMsg($("#upload-msg"), "Upload complete.", "ok");
    $("#file").value = "";
    await loadProjects();
    selectProject(project);
  } catch (e) {
    setMsg($("#upload-msg"), e.message, "error");
  } finally {
    $("#upload-btn").disabled = false;
    $("#upload-progress").classList.add("hidden");
  }
};

// ---------- projects ----------
async function loadProjects() {
  const projects = await api("GET", "/api/projects");
  const box = $("#projects");
  box.replaceChildren();
  if (!projects.length) {
    box.append(el("p", { className: "muted", textContent: "No projects yet." }));
    return;
  }
  projects.forEach((p) => {
    const row = el("div", { className: "project-row" + (currentProject && currentProject.id === p.id ? " selected" : "") });
    const info = el("div");
    info.append(el("strong", { textContent: p.original_filename }), el("div", {
      className: "muted",
      textContent: `${fmtTime(p.duration)} | ${p.width}x${p.height} | ${fmtSize(p.size_bytes)}`,
    }));
    row.append(info, el("span", { className: "muted", textContent: "Open" }));
    row.onclick = () => selectProject(p);
    box.append(row);
  });
}

function selectProject(p) {
  currentProject = p;
  lastSig = "";
  $("#project-panel").classList.remove("hidden");
  $("#project-title").textContent = p.original_filename;
  $("#project-meta").textContent = `${fmtTime(p.duration)} | ${p.width}x${p.height} | ${fmtSize(p.size_bytes)}`;
  setMsg($("#clip-msg"), "");
  loadProjects();
  refreshClips();
  clearInterval(pollTimer);
  pollTimer = setInterval(refreshClips, 1500);
}

$("#delete-project").onclick = async () => {
  if (!currentProject || !confirm("Delete this project, its source video and all clips?")) return;
  try {
    await api("DELETE", `/api/projects/${currentProject.id}`);
    currentProject = null;
    clearInterval(pollTimer);
    $("#project-panel").classList.add("hidden");
    loadProjects();
  } catch (e) {
    setMsg($("#clip-msg"), e.message, "error");
  }
};

// ---------- clip settings ----------
$("#duration").onchange = () => {
  $("#custom-wrap").classList.toggle("hidden", $("#duration").value !== "custom");
};

$("#make-clips").onclick = async () => {
  const start = parseTime($("#start").value);
  const duration = $("#duration").value === "custom"
    ? Number($("#custom-duration").value) : Number($("#duration").value);
  const count = parseInt($("#count").value, 10);
  if (start === null || !(duration >= 1) || !(count >= 1)) {
    return setMsg($("#clip-msg"), "Check the start time, duration and number of clips.", "error");
  }
  try {
    const r = await api("POST", `/api/projects/${currentProject.id}/clips`,
      { start, duration, count, burn_captions: $("#burn-captions").checked });
    setMsg($("#clip-msg"), `Queued ${r.clips.length} clip(s). They render one at a time.`, "ok");
    refreshClips();
  } catch (e) {
    setMsg($("#clip-msg"), e.message, "error");
  }
};

// ---------- clips ----------
async function refreshClips() {
  if (!currentProject) return;
  try {
    renderClips(await api("GET", `/api/projects/${currentProject.id}/clips`));
  } catch (_) {}
}

function renderClips(clips) {
  const sig = clips.map((c) => `${c.id}:${c.status}`).join("|");
  if (sig !== lastSig) {
    lastSig = sig;
    const box = $("#clips");
    box.replaceChildren();
    if (!clips.length) {
      box.append(el("p", { className: "muted", textContent: "No clips yet." }));
      return;
    }
    clips.forEach((c) => box.append(buildClip(c)));
  }
  clips.forEach((c) => {
    const bar = document.querySelector(`#clip-${c.id} progress`);
    if (bar) bar.value = c.progress;
  });
}

function buildClip(c) {
  const box = el("div", { className: "clip", id: `clip-${c.id}` });
  const head = el("div", { className: "clip-head" });
  const badges = el("span");
  if (c.burn_captions) badges.append(el("span", { className: "badge", textContent: "captions " }), " ");
  badges.append(el("span", { className: "badge " + c.status, textContent: c.status }));
  head.append(
    el("strong", { textContent: `${c.name} (${fmtTime(c.end_time - c.start_time)})` }),
    badges,
  );
  box.append(head);

  const active = c.status === "queued" || c.status === "processing";
  if (active) box.append(el("progress", { max: 1, value: c.progress }));
  if (c.status === "failed" && c.error) box.append(el("p", { className: "msg error", textContent: c.error }));
  if (c.note) box.append(el("p", { className: "muted", textContent: c.note }));
  if (c.status === "done") {
    box.append(el("video", { controls: true, preload: "metadata", src: `/api/clips/${c.id}/file` }));
    if (c.encoder) box.append(el("p", { className: "muted", textContent: `Encoded with ${c.encoder}` }));
  }

  const actions = el("div", { className: "actions" });
  if (active) {
    actions.append(el("button", { className: "secondary", textContent: "Cancel", onclick: () => clipAction("POST", `/api/clips/${c.id}/cancel`) }));
  } else {
    if (c.status === "done") {
      actions.append(el("button", { textContent: "Download", onclick: () => { location.href = `/api/clips/${c.id}/file?download=1`; } }));
      actions.append(el("button", { className: "secondary", textContent: "Download .srt", onclick: () => downloadClipSrt(c) }));
    }
    actions.append(el("button", { className: "danger", textContent: "Delete", onclick: () => clipAction("DELETE", `/api/clips/${c.id}`) }));
  }
  box.append(actions);
  return box;
}

async function downloadClipSrt(c) {
  try {
    const res = await fetch(`/api/clips/${c.id}/captions.srt`);
    if (!res.ok) {
      let d = {};
      try { d = await res.json(); } catch (_) {}
      throw new Error(d.error || `Could not export subtitles (${res.status})`);
    }
    const url = URL.createObjectURL(await res.blob());
    const a = el("a", { href: url, download: `${c.name}.srt` });
    document.body.append(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  } catch (e) {
    setMsg($("#clip-msg"), e.message, "error");
  }
}

async function clipAction(method, url) {
  try { await api(method, url); } catch (e) { setMsg($("#clip-msg"), e.message, "error"); }
  lastSig = "";
  refreshClips();
}

loadProjects();