// Stock-footage discovery: script-aware searches inspired by MoneyPrinterTurbo.
// This is a separate extension so the original B-roll/editor controls stay intact.
(() => {
  const panel = document.getElementById("project-panel");
  if (!panel) return;
  const section = document.createElement("section");
  section.className = "card";
  section.innerHTML = `
    <h3>Find real B-roll footage</h3>
    <p class="muted">Ollama reads your clip and suggests scene-specific search phrases in story order.
    Search Pixabay for real footage and import only the videos you choose. No synthetic AI videos.</p>
    <div class="actions">
      <button type="button" id="stock-plan">Find footage ideas from clip</button>
      <input type="text" id="stock-query" maxlength="80" placeholder="Search real footage">
      <button type="button" id="stock-search">Search Pixabay</button>
    </div>
    <p id="stock-message" class="msg" role="status"></p>
    <div id="stock-ideas"></div>
    <div id="stock-results" class="form-grid"></div>
    <p class="muted">Pixabay search requires a free API key set locally as PIXABAY_API_KEY.
    Review usage rights and verify footage before posting.</p>`;
  panel.append(section);
  const $ = id => document.getElementById(id);
  let projectAtStart = null;
  const say = text => { $("stock-message").textContent = text; };
  const currentId = () => (typeof currentProject !== "undefined" && currentProject ? currentProject.id : null);
  const makeButton = (label, fn) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "secondary";
    button.textContent = label;
    button.addEventListener("click", fn);
    return button;
  };
  $("stock-plan").addEventListener("click", async () => {
    const cid = $("broll-clip")?.value;
    if (!currentId() || !cid) { say("Select a project and a clip in the B-roll library first."); return; }
    say("Ollama is reading the clip and choosing realistic footage ideas...");
    $("stock-ideas").replaceChildren();
    $("stock-plan").disabled = true;
    try {
      const response = await fetch(`/api/clips/${cid}/broll/shot-plan`, {method:"POST"});
      const data = await response.json();
      if (!response.ok) throw Error(data.error || "Could not generate footage ideas.");
      say(data.shots.length ? "Choose a scene below to search for real footage." : "No useful B-roll needed for this clip.");
      for (const shot of data.shots) {
        const row = document.createElement("div");
        row.style.cssText = "padding:10px 0;border-bottom:1px solid var(--border)";
        const description = document.createElement("p");
        description.textContent = `${shot.start.toFixed(1)}s — ${shot.dialogue}`;
        const reason = document.createElement("p");
        reason.className = "muted";
        reason.textContent = shot.reason;
        row.append(description, reason, makeButton(`Search: ${shot.query}`, () => {
          $("stock-query").value = shot.query;
          $("stock-search").click();
        }));
        $("stock-ideas").append(row);
      }
    } catch (error) { say(error.message); }
    finally { $("stock-plan").disabled = false; }
  });
  $("stock-search").addEventListener("click", async () => {
    const query = $("stock-query").value.trim();
    const project = currentId();
    if (!project) { say("Select a project first."); return; }
    if (query.length < 2) { say("Enter a footage search phrase."); return; }
    projectAtStart = project;
    $("stock-results").replaceChildren();
    $("stock-search").disabled = true;
    say(`Searching Pixabay for "${query}"...`);
    try {
      const response = await fetch(`/api/broll/stock/search?q=${encodeURIComponent(query)}`);
      const data = await response.json();
      if (!response.ok) throw Error(data.error || "Search failed.");
      if (projectAtStart !== currentId()) return;
      say(`Found ${data.results.length} stock-video candidates. Import only footage that fits the clip.`);
      for (const result of data.results.slice(0, 12)) {
        const card = document.createElement("div");
        card.className = "card";
        const preview = document.createElement("video");
        preview.controls = true;
        preview.preload = "none";
        preview.style.cssText = "max-width:100%;max-height:180px";
        preview.src = result.download_url;
        const detail = document.createElement("p");
        detail.textContent = `${result.duration || "?"}s · ${result.width || "?"}×${result.height || "?"} · ${result.creator || "Pixabay contributor"}`;
        const description = document.createElement("input");
        description.type = "text";
        description.maxLength = 160;
        description.value = query;
        description.placeholder = "Describe this scene for future matching";
        description.setAttribute("aria-label", "Footage description");
        const importButton = makeButton("Import to my B-roll library", async () => {
          if (currentId() !== projectAtStart) { say("Please select the original project again."); return; }
          importButton.disabled = true;
          say("Importing the selected footage to your local library...");
          try {
            const response = await fetch(`/api/projects/${projectAtStart}/broll/import-stock`, {
              method:"POST", headers:{"Content-Type":"application/json"},
              body: JSON.stringify({
                url:result.download_url,
                description:description.value,
                source_page:result.preview
              })
            });
            const data = await response.json();
            if (!response.ok) throw Error(data.error || "Could not import this video.");
            importButton.textContent = "Imported";
            say("Footage imported. Click Refresh in the B-roll library to see it.");
          } catch (error) {
            say(error.message);
            importButton.disabled = false;
          }
        });
        card.append(preview, detail, description, importButton);
        if (result.preview && /^https:\/\//.test(result.preview)) {
          const a = document.createElement("a");
          a.href = result.preview;
          a.textContent = "View original on Pixabay";
          a.target = "_blank";
          a.rel = "noopener noreferrer";
          card.append(a);
        }
        $("stock-results").append(card);
      }
    } catch (error) { say(error.message); }
    finally { $("stock-search").disabled = false; }
  });
})();