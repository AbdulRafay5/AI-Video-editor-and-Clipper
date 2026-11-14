// Free, local B-roll asset library. The editing/render integration comes later.
(() => {
  const panel = document.getElementById("project-panel");
  if (!panel) return;
  const section = document.createElement("section");
  section.className = "card";
  section.innerHTML = `
    <h3>B-roll library (local)</h3>
    <p class="muted">Add your own reusable footage and images. Stored on this computer, not uploaded to an AI service. Automatic B-roll placement is coming in a separate step.</p>
    <div class="actions">
      <input id="broll-file" type="file" accept=".mp4,.mov,.mkv,.webm,.png,.jpg,.jpeg,.webp">
      <button id="broll-add" type="button">Add footage</button>
      <button id="broll-refresh" class="secondary" type="button">Refresh</button>
    </div>
    <p id="broll-message" class="msg" role="status"></p>
    <div id="broll-items" class="form-grid"></div>`;
  panel.append(section);
  const $ = id => document.getElementById(id);
  const message = msg => { $("broll-message").textContent = msg; };
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
        label.textContent = `${item.kind} · ${(item.bytes/1048576).toFixed(1)} MB`;
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
        box.append(media, label, remove);
        list.append(box);
      }
      if (!data.items.length) list.textContent = "No B-roll added to this project yet.";
    } catch (e) { message(e.message); }
  }
  $("broll-refresh").addEventListener("click", refresh);
  $("broll-add").addEventListener("click", async () => {
    const pid = currentProjectId();
    const file = $("broll-file").files[0];
    if (!pid || !file) { message("Select a project and choose a file."); return; }
    if (file.size > 200 * 1024 * 1024) { message("Maximum file size is 200 MB."); return; }
    const form = new FormData();
    form.append("file", file);
    message("Adding footage...");
    try {
      const res = await fetch(`/api/projects/${pid}/broll`, {method:"POST",body:form});
      const data = await res.json();
      if (!res.ok) throw Error(data.error || "Upload failed.");
      $("broll-file").value = "";
      message("Footage added to the local library.");
      refresh();
    } catch (e) { message(e.message); }
  });
  new MutationObserver(() => {
    if (!panel.classList.contains("hidden")) refresh();
  }).observe(panel, {attributes:true,attributeFilter:["class"]});
})();