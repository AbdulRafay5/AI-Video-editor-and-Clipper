// Free, local B-roll asset library. The editing/render integration comes later.
(() => {
  const panel = document.getElementById("project-panel");
  if (!panel) return;
  const section = document.createElement("section");
  section.className = "card";
  section.innerHTML = `
    <h3>B-roll library (local)</h3>
    <p class="muted">Add your own reusable footage and images. Stored on this computer, not uploaded to an AI service. Add descriptions, then generate transcript-matched suggestions. Choose suggested cutaways, render a separate copy, and download it. The original clip remains unchanged.</p>
    <div class="actions">
      <input id="broll-file" type="file" accept=".mp4,.mov,.mkv,.webm,.png,.jpg,.jpeg,.webp">
      <button id="broll-add" type="button">Add footage</button>
      <input id="broll-description" type="text" maxlength="160" placeholder="Describe the scene (optional)" aria-label="B-roll description">
      <button id="broll-refresh" class="secondary" type="button">Refresh</button>
    </div>
    <p id="broll-message" class="msg" role="status"></p>
    <div id="broll-items" class="form-grid"></div>
    <h4>Suggested cutaways</h4>
    <p class="muted">Choose a clip and find relevant B-roll moments. Suggestions are not applied automatically.</p>
    <div class="actions"><select id="broll-clip" aria-label="Clip to analyse"></select>
      <button type="button" id="broll-suggest">Suggest cutaways</button></div>
    <div id="broll-suggestions" role="status"></div>
    <div class="actions"><button type="button" id="broll-render" disabled>Render selected cutaways</button></div>
    <p id="broll-render-status" class="msg" role="status"></p>
    <div id="broll-download"></div>
    <p class="muted">B-roll preserves the original speech. It can cover burned-in captions while visible; caption layering will be improved later. Recheck campaign rules before posting.</p>`;
  panel.append(section);
  const $ = id => document.getElementById(id);
  const message = msg => { $("broll-message").textContent = msg; };
  let planned = [];
  let plannedClip = null;
  function currentProjectId() {
    // Project selection is reflected by the layout settings request and project title.
    // Use the page's selectedProject global, if present.
    const p = typeof currentProject !== "undefined" ? currentProject : null;
    return p && (p.id || p);
  }
  async function refresh() {
    const pid = currentProjectId();
    if (!pid) { $("broll-items").textContent = "Select a project first."; return; }
    try {
      const r = await fetch(`/api/projects/${pid}/broll`);
      const data = await r.json();
      if (!r.ok) throw Error(data.error || "Could not load footage.");
      const list = $("broll-items");
      list.replaceChildren();
      for (const item of data.items) {
        const box = document.createElement("div");
        box.className = "card";
        const media = document.createElement(item.kind === "video" ? "video" : "img");
        media.src = item.url;
        media.style.cssText = "max-width:100%;max-height:180px;object-fit:contain";
        if (item.kind === "video") { media.controls = true; media.preload = "metadata"; }
        else media.alt = "B-roll image";
        const label = document.createElement("p");
        label.textContent = `${item.name} · ${item.kind} · ${(item.bytes/1048576).toFixed(1)} MB`;
        const description = document.createElement("input");
        description.type = "text";
        description.maxLength = 160;
        description.value = item.description || "";
        description.placeholder = "What does this footage show?";
        description.setAttribute("aria-label", `Description for ${item.name}`);
        const saveDescription = document.createElement("button");
        saveDescription.type = "button";
        saveDescription.className = "secondary";
        saveDescription.textContent = "Save description";
        saveDescription.addEventListener("click", async () => {
          const res = await fetch(`/api/projects/${pid}/broll/${item.id}`, {
            method: "PATCH", headers: {"Content-Type": "application/json"},
            body: JSON.stringify({description: description.value})
          });
          message(res.ok ? "Description saved." : "Could not save the description.");
        });
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "secondary";
        remove.textContent = "Remove";
        remove.addEventListener("click", async () => {
          if (!confirm("Remove this B-roll asset?")) return;
          const res = await fetch(`/api/projects/${pid}/broll/${item.id}`, {method:"DELETE"});
          if (!res.ok) { message("Could not delete the asset."); return; }
          refresh();
        });
        box.append(media, label, description, saveDescription, remove);
        list.append(box);
      }
      if (!data.items.length) list.textContent = "No B-roll added to this project yet.";
    } catch (e) { message(e.message); }
  }
  // Switching between already-open projects does not change the panel's CSS class.
  document.addEventListener("click", event => {
    if (event.target.closest(".project-row")) setTimeout(() => { refresh(); loadClips(); planned=[]; plannedClip=null; $("broll-render").disabled=true; $("broll-suggestions").replaceChildren(); $("broll-download").replaceChildren(); }, 0);
  });
  async function loadClips() {
    const pid = currentProjectId();
    if (!pid) return;
    try {
      const res = await fetch(`/api/projects/${pid}/clips`);
      const clips = await res.json();
      if (!res.ok || !Array.isArray(clips)) return;
      const select = $("broll-clip");
      const selected = select.value;
      select.replaceChildren();
      for (const clip of clips) {
        const opt = document.createElement("option");
        opt.value = String(clip.id);
        opt.textContent = `${clip.name} (${Math.round(clip.end_time - clip.start_time)}s)`;
        select.append(opt);
      }
      if ([...select.options].some(o => o.value === selected)) select.value = selected;
    } catch (_) { message("Could not load clips."); }
  }
  $("broll-suggest").addEventListener("click", async () => {
    const cid = $("broll-clip").value;
    planned = [];
    plannedClip = null;
    $("broll-render").disabled = true;
    $("broll-download").replaceChildren();
    $("broll-render-status").textContent = "";
    const out = $("broll-suggestions");
    if (!cid) { out.textContent = "Create a clip first."; return; }
    out.textContent = "Matching footage to transcript...";
    try {
      const res = await fetch(`/api/clips/${cid}/broll/suggestions`);
      const data = await res.json();
      if (!res.ok) throw Error(data.error || "Could not generate suggestions.");
      out.replaceChildren();
      const note = document.createElement("p");
      note.textContent = data.message;
      out.append(note);
      plannedClip = cid;
      data.suggestions.forEach((suggestion, index) => {
        const row = document.createElement("label");
        row.style.cssText = "display:block;padding:8px 0";
        const check = document.createElement("input");
        check.type = "checkbox";
        check.checked = true;
        check.addEventListener("change", () => {
          planned[index].enabled = check.checked;
          $("broll-render").disabled = !planned.some(v => v.enabled);
        });
        planned.push({asset_id:suggestion.asset_id, start:suggestion.start,
                      end:suggestion.end, enabled:true});
        row.append(check, document.createTextNode(
          ` ${suggestion.start.toFixed(1)}–${suggestion.end.toFixed(1)}s: ${suggestion.asset_name} (matched: ${suggestion.matched_terms.join(", ")})`));
        out.append(row);
      });
      $("broll-render").disabled = !planned.length;
    } catch (err) { out.textContent = err.message; }
  });
  $("broll-render").addEventListener("click", async () => {
    const cid = $("broll-clip").value;
    const cutaways = planned.filter(p => p.enabled).map(({asset_id,start,end}) => ({asset_id,start,end}));
    if (!cid || cid !== plannedClip || !cutaways.length) {
      $("broll-render-status").textContent = "Choose a clip, suggest footage, and select cutaways first.";
      return;
    }
    const button = $("broll-render");
    button.disabled = true;
    $("broll-download").replaceChildren();
    const status = $("broll-render-status");
    status.textContent = "Rendering a separate B-roll version...";
    try {
      const response = await fetch(`/api/clips/${cid}/broll/render`, {
        method:"POST", headers:{"Content-Type":"application/json"},
        body:JSON.stringify({cutaways})
      });
      const started = await response.json();
      if (!response.ok) throw Error(started.error || "Could not start export.");
      for (let attempt=0; attempt<600; attempt++) {
        await new Promise(resolve => setTimeout(resolve, 1000));
        const stateResponse = await fetch(`/api/clips/${cid}/broll/render`);
        const state = await stateResponse.json();
        if (!stateResponse.ok) throw Error(state.error || "Could not check render progress.");
        if (state.id !== started.job_id) throw Error("Another export replaced this job.");
        if (state.status === "failed") throw Error(state.error || "Render failed.");
        status.textContent = `Rendering B-roll: ${Math.round(state.progress*100)}%`;
        if (state.status === "done") {
          status.textContent = "B-roll version ready. Your original video is untouched.";
          const link = document.createElement("a");
          link.href = state.download_url;
          link.textContent = "Download video with B-roll";
          link.setAttribute("download", "");
          $("broll-download").append(link);
          break;
        }
        if (attempt === 599) throw Error("Export is taking unusually long. Check the app logs.");
      }
    } catch (error) {
      status.textContent = error.message;
    } finally {
      button.disabled = !planned.some(v => v.enabled);
    }
  });
  $("broll-refresh").addEventListener("click", () => { refresh(); loadClips(); });
  $("broll-add").addEventListener("click", async () => {
    const pid = currentProjectId();
    const file = $("broll-file").files[0];
    if (!pid || !file) { message("Select a project and choose a file."); return; }
    if (file.size > 200 * 1024 * 1024) { message("Maximum file size is 200 MB."); return; }
    const form = new FormData();
    form.append("file", file);
    form.append("description", $("broll-description").value);
    message("Adding footage...");
    try {
      const res = await fetch(`/api/projects/${pid}/broll`, {method:"POST",body:form});
      const data = await res.json();
      if (!res.ok) throw Error(data.error || "Upload failed.");
      $("broll-file").value = "";
      $("broll-description").value = "";
      message("Footage added to the local library.");
      refresh();
    } catch (e) { message(e.message); }
  });
  new MutationObserver(() => {
    if (!panel.classList.contains("hidden")) { refresh(); loadClips(); }
  }).observe(panel, {attributes:true,attributeFilter:["class"]});
})();