
(() => {
  const main = document.querySelector("main");
  if (!main) return;
  const area = document.createElement("section");
  area.className = "card";
  area.id = "creator-studio";
  area.style.cssText = "margin:24px 0;padding:22px";
  area.innerHTML = `
    <h2>Creator Studio</h2>
    <p class="muted">Start from an idea, not a video upload. Local Ollama generates an editable script and scenes.
    Generate offline narration from your script. Visuals and rendering are the next production stages.</p>
    <div class="form-grid">
      <label>Video topic <textarea id="creator-topic" rows="2" maxlength="300" placeholder="Explain how a small business got its first 100 customers"></textarea></label>
      <label>Audience <input type="text" id="creator-audience" maxlength="100" value="general"></label>
      <label>Target length <select id="creator-length">
        <option value="30">30 seconds</option><option value="60" selected>60 seconds</option>
        <option value="90">90 seconds</option><option value="120">2 minutes</option>
      </select></label>
    </div>
    <div class="actions"><button id="creator-create" type="button">Create video project</button>
      <button id="creator-refresh" type="button" class="secondary">Refresh projects</button></div>
    <p id="creator-message" class="msg" role="status"></p>
    <div id="creator-projects" class="form-grid"></div>
    <div id="creator-details"></div>`;
  main.append(area);
  const $ = id => document.getElementById(id);
  const say = s => { $("creator-message").textContent = s; };
  let selected = null;
  let polling = null;
  const request = async (method, path, body) => {
    const r = await fetch(path, {method, headers: body===undefined ? {} : {"Content-Type":"application/json"},
      body: body===undefined ? undefined : JSON.stringify(body)});
    const data = await r.json();
    if(!r.ok) throw Error(data.error || "Request failed.");
    return data;
  };
  async function refreshList() {
    try {
      const data = await request("GET", "/api/creator/projects");
      const list = $("creator-projects");
      list.replaceChildren();
      if (!data.projects.length) list.textContent = "No original video projects yet.";
      for (const project of data.projects) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "secondary";
        button.textContent = project.topic + " · " + project.status;
        button.onclick = () => openProject(project.id);
        list.append(button);
      }
    } catch (err) { say(err.message); }
  }
  function field(text, value, rows=2) {
    const label = document.createElement("label");
    label.textContent = text;
    const input = document.createElement("textarea");
    input.rows = rows;
    input.value = value || "";
    label.append(input);
    return [label, input];
  }
  function stopPolling() {
    if(polling) clearInterval(polling);
    polling=null;
  }
  async function openProject(id) {
    selected = id;
    stopPolling();
    try {
      const {project} = await request("GET", "/api/creator/projects/" + id);
      if(selected !== id) return;
      renderProject(project);
      if(["generating_script","generating_narration","rendering"].includes(project.status)) {
        polling=setInterval(async () => {
          if(selected !== id) return stopPolling();
          try {
            const {project:p} = await request("GET", "/api/creator/projects/" + id);
            if(!["generating_script","generating_narration","rendering"].includes(p.status)) {
              stopPolling();
              renderProject(p);
              refreshList();
            }
          } catch (err) { stopPolling(); say(err.message); }
        }, 2500);
      }
    } catch (err) { say(err.message); }
  }
  function renderProject(project) {
    const container = $("creator-details");
    container.replaceChildren();
    const title = document.createElement("h3");
    title.textContent = "Video: " + project.topic;
    const status = document.createElement("p");
    status.textContent = "Status: " + project.status + (project.error ? " — " + project.error : "");
    container.append(title, status);
    const steps = document.createElement("p");
    steps.className = "muted";
    const names = ["script","narration","visuals","captions","music","render"];
    steps.textContent = names.map(name => {
      const stage=project.stages[name];
      return name + " (" + (stage ? stage.status : "pending") + ")";
    }).join(" · ");
    container.append(steps);
    const buttons = document.createElement("div");
    buttons.className = "actions";
    const generate = document.createElement("button");
    generate.type = "button";
    generate.disabled = ["generating_script","generating_narration","rendering"].includes(project.status);
    generate.textContent = project.script ? "Regenerate script" : "Generate script with local Ollama";
    generate.onclick = async () => {
      if(project.script && !confirm("Replace the current script? Save a copy elsewhere if needed.")) return;
      generate.disabled = true;
      try {
        await request("POST", "/api/creator/projects/" + project.id + "/script/generate");
        say("Ollama is drafting the script...");
        openProject(project.id);
      } catch(err) { generate.disabled=false; say(err.message); }
    };
    buttons.append(generate);
    const remove = document.createElement("button");
    remove.type = "button"; remove.className = "secondary";
    remove.textContent = "Delete project";
    remove.disabled = ["generating_script","generating_narration","rendering"].includes(project.status);
    remove.onclick = async () => {
      if(!confirm("Delete this original video project?")) return;
      try {
        await request("DELETE", "/api/creator/projects/" + project.id);
        selected=null; stopPolling(); container.replaceChildren(); refreshList();
      } catch(err) { say(err.message); }
    };
    buttons.append(remove);
    container.append(buttons);
    if(!project.script) return;
    const voiceBox = document.createElement("div");
    voiceBox.className = "card";

    const voiceTitle = document.createElement("h4");
    voiceTitle.textContent = "Neural narration · free";
    const voiceInfo = document.createElement("p");
    voiceInfo.className = "muted";
    voiceInfo.textContent = "Kokoro runs locally; Edge TTS uses the Internet without an API key. Piper and Windows voices remain available.";
    const engineLabel = document.createElement("label");
    engineLabel.textContent = "Voice engine ";
    const engineSelect = document.createElement("select");
    engineSelect.setAttribute("aria-label", "Narration engine");
    engineLabel.append(engineSelect);
    const voiceLabel = document.createElement("label");
    voiceLabel.textContent = "Voice ";
    const voiceSelect = document.createElement("select");
    voiceSelect.setAttribute("aria-label", "Narration voice");
    voiceLabel.append(voiceSelect);
    const voiceButton = document.createElement("button");
    voiceButton.type = "button";
    voiceButton.disabled = true;
    voiceButton.textContent = project.stages.narration?.status === "done" ? "Regenerate narration" : "Generate narration";
    let catalog = [];
    function syncVoiceOptions() {
      const chosen = catalog.find(e => e.name === engineSelect.value);
      voiceSelect.replaceChildren();
      if (!chosen) return;
      for (const voice of chosen.voices) {
        const option = document.createElement("option");
        option.value = voice;
        option.textContent = voice;
        voiceSelect.append(option);
      }
      if(chosen.voices.includes(chosen.voice)) voiceSelect.value = chosen.voice;
      voiceInfo.textContent = chosen.description + (chosen.online
        ? " · Free, Internet connection required" : " · Free, runs locally");
      voiceButton.disabled = !chosen.ready || ["generating_script","generating_narration","rendering"].includes(project.status);
    }
    engineSelect.addEventListener("change", syncVoiceOptions);
    const audioList = document.createElement("div");
    voiceBox.append(voiceTitle, voiceInfo, engineLabel, voiceLabel, voiceButton, audioList);
    container.append(voiceBox);
    request("GET", "/api/creator/narration/capabilities").then(data => {
      if(selected !== project.id) return;
      catalog = data.engines || [];
      engineSelect.replaceChildren();
      for (const engine of catalog) {
        const option = document.createElement("option");
        option.value = engine.name;
        option.textContent = engine.name + (engine.ready ? "" : " (not configured)");
        engineSelect.append(option);
      }
      engineSelect.value = data.default || catalog[0]?.name || "";
      syncVoiceOptions();
    }).catch(err => { if(selected===project.id) voiceInfo.textContent = err.message; });
    voiceButton.onclick = async () => {
      if(project.stages.narration?.status === "done" && !confirm("Regenerate the current narration from the saved script?")) return;
      voiceButton.disabled = true;
      try {
        const selection = {engine:engineSelect.value, voice:voiceSelect.value};
        await request("POST", "/api/creator/projects/" + project.id + "/narration/generate", selection);
        say("Generating narration with " + selection.engine + "...");
        openProject(project.id);
      } catch(err) { say(err.message); voiceButton.disabled=false; }
    };
    if(project.stages.narration?.status === "done") {
      request("GET", "/api/creator/projects/" + project.id + "/narration").then(data => {
        if(selected !== project.id || !data.narration?.artifact) return;
        const artifact = data.narration.artifact;
        const note = document.createElement("p");
        note.textContent = "Narration duration: " + Number(artifact.duration).toFixed(1)
          + " seconds · " + artifact.segments.length + " parts";
        const player = document.createElement("audio");
        player.controls = true;
        player.preload = "none";
        player.src = "/api/creator/projects/" + project.id + "/narration/audio";
        const download = document.createElement("a");
        download.href = player.src;
        download.textContent = "Download narration WAV";
        download.setAttribute("download", "narration.wav");
        audioList.append(note, player, document.createTextNode(" "), download);
        artifact.segments.forEach((segment, index) => {
          const details = document.createElement("details");
          const summary = document.createElement("summary");
          summary.textContent = (segment.kind === "scene" ? "Scene " + (segment.scene_index + 1) : segment.kind)
            + " · " + Number(segment.start).toFixed(1) + "–" + Number(segment.end).toFixed(1) + "s";
          const audio = document.createElement("audio");
          audio.controls = true;
          audio.preload = "none";
          audio.src = player.src + "?segment=" + index;
          details.append(summary, audio);
          audioList.append(details);
        });
      }).catch(err => { if(selected===project.id) say(err.message); });
    }
    const script = project.script;
    const visualBox = document.createElement("section");
    visualBox.className = "card";
    const visualTitle = document.createElement("h4");
    visualTitle.textContent = "Scene footage · local and free";
    const visualHint = document.createElement("p");
    visualHint.className = "muted";
    visualHint.textContent = "Upload MP4, MOV or MKV footage for the opening, each scene and ending. No paid services or automatic downloads.";
    const visualList = document.createElement("div");
    visualBox.append(visualTitle, visualHint, visualList);
    const renderButton = document.createElement("button");
    renderButton.type = "button";
    renderButton.textContent = "Render complete video";
    renderButton.disabled = true;
    const renderMessage = document.createElement("p");
    renderMessage.className = "muted";
    visualBox.append(renderButton, renderMessage);
    container.append(visualBox);
    const slots = ["hook", ...script.scenes.map((_, index) => "scene_" + index), "closing"];
    function drawSlots(clips) {
      visualList.replaceChildren();
      for (const slot of slots) {
        const wrap = document.createElement("div");
        wrap.style.cssText = "display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin:12px 0";
        const label = document.createElement("strong");
        label.textContent = slot === "hook" ? "Opening" : slot === "closing" ? "Ending" : "Scene " + (Number(slot.split("_")[1]) + 1);
        const file = document.createElement("input");
        file.type = "file"; file.accept = ".mp4,.mov,.mkv,video/*";
        file.setAttribute("aria-label", "Footage for " + label.textContent);
        const upload = document.createElement("button");
        upload.type = "button"; upload.className = "secondary";
        upload.textContent = clips[slot] ? "Replace footage" : "Upload footage";
        upload.disabled = ["generating_script","generating_narration","rendering"].includes(project.status);
        const state = document.createElement("span");
        state.textContent = clips[slot] ? "Saved" : "Missing";
        upload.onclick = async () => {
          if(!file.files.length) return say("Choose footage for " + label.textContent + " first.");
          if(file.files[0].size > 250 * 1024 * 1024) return say("Video must be under 250MB.");
          upload.disabled = true;
          state.textContent = "Uploading...";
          try {
            const form = new FormData();
            form.append("video", file.files[0]);
            const response = await fetch("/api/creator/projects/" + project.id + "/visuals/" + slot,
              {method: "POST", body: form});
            const result = await response.json();
            if(!response.ok) throw Error(result.error || "Upload failed.");
            say("Footage saved for " + label.textContent);
            if(selected === project.id) openProject(project.id);
          } catch (err) {
            state.textContent = err.message;
            upload.disabled = false;
          }
        };
        wrap.append(label, file, upload, state);
        visualList.append(wrap);
      }
      const ready = slots.every(slot => Boolean(clips[slot]));
      renderButton.disabled = !ready ||
        project.stages.narration?.status !== "done" ||
        ["generating_script","generating_narration","rendering"].includes(project.status);
      renderMessage.textContent = ready
        ? "All scene footage uploaded. Narration is required to render."
        : "Upload footage for each part of the script to enable rendering.";
    }
    request("GET", "/api/creator/projects/" + project.id + "/visuals")
      .then(data => { if(selected === project.id) drawSlots(data.clips || {}); })
      .catch(err => { if(selected === project.id) renderMessage.textContent = err.message; });
    renderButton.onclick = async () => {
      if(!confirm("Render your video locally using the MoneyPrinterTurbo project renderer? This can use significant CPU and disk space.")) return;
      renderButton.disabled = true;
      try {
        await request("POST", "/api/creator/projects/" + project.id + "/render");
        say("Rendering locally. You can return to this project to check progress.");
        openProject(project.id);
      } catch (err) { say(err.message); renderButton.disabled = false; }
    };
    if (project.stages.render?.status === "done") {
      const link = document.createElement("a");
      link.href = "/api/creator/projects/" + project.id + "/render/file";
      link.textContent = "Download completed MP4";
      link.setAttribute("download", "creator_" + project.id + ".mp4");
      visualBox.append(link);
    }
    const [titleLabel, titleInput] = field("Title", script.title, 1);
    const [hookLabel, hookInput] = field("Opening hook", script.hook, 2);
    container.append(titleLabel, hookLabel);
    const sceneFields = [];
    script.scenes.forEach((scene,index) => {
      const box = document.createElement("div");
      box.className = "card";
      const heading = document.createElement("h4");
      heading.textContent = "Scene " + (index+1);
      const [nLabel, nInput] = field("Narration", scene.narration, 3);
      const [vLabel, vInput] = field("Visual idea", scene.visual_idea, 2);
      box.append(heading, nLabel, vLabel);
      container.append(box);
      sceneFields.push({nInput,vInput});
    });
    const [closingLabel, closingInput] = field("Closing", script.closing, 2);
    container.append(closingLabel);
    const save = document.createElement("button");
    save.type = "button";
    save.textContent = "Save script changes";
    save.onclick = async () => {
      save.disabled = true;
      try {
        await request("PUT", "/api/creator/projects/" + project.id + "/script", {
          title:titleInput.value, hook:hookInput.value,
          scenes:sceneFields.map(({nInput,vInput}) => ({
            narration:nInput.value, visual_idea:vInput.value
          })), closing:closingInput.value
        });
        say("Script saved. Future stages will use this version.");
        refreshList();
      } catch(err) { say(err.message); }
      finally { save.disabled=false; }
    };
    container.append(save);
  }
  $("creator-create").onclick = async () => {
    const create = $("creator-create");
    create.disabled = true;
    try {
      const data = await request("POST", "/api/creator/projects", {
        topic:$("creator-topic").value,
        audience:$("creator-audience").value,
        target_seconds:Number($("creator-length").value)
      });
      say("Video project created. Generate a script to begin.");
      await refreshList();
      await openProject(data.project.id);
    } catch(err) { say(err.message); }
    finally { create.disabled=false; }
  };
  $("creator-refresh").onclick = refreshList;
  refreshList();
})();
