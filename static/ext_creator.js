
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
    <section class="card" aria-label="Batch video production">
      <h3>Batch video production</h3>
      <p class="muted">Create 2–8 video projects. Jobs run one at a time to protect local GPU memory. Each output requires review before publishing.</p>
      <label>Topics · one per line
        <textarea id="creator-batch-topics" rows="4" placeholder="How coffee is roasted&#10;Five facts about ocean life"></textarea>
      </label>
      <div class="form-grid">
        <label>Visual source
          <select id="creator-batch-mode">
            <option value="stock">Free stock footage</option>
            <option value="ai" selected>AI images + motion</option>
            <option value="neural_video">True neural video · experimental</option>
          </select>
        </label>
        <label>Soundtrack
          <select id="creator-batch-music">
            <option value="none" selected>No music</option>
            <option value="instrumental">Free local instrumental</option>
            <option value="neural">MusicGen · experimental</option>
          </select>
        </label>
      </div>
      <label><input id="creator-batch-download" type="checkbox"> Approve first-use downloads for the selected visual model or stock footage</label>
      <div class="actions">
        <button id="creator-batch-start" type="button">Generate batch</button>
        <button id="creator-batch-refresh" type="button" class="secondary">Refresh batches</button>
      </div>
      <p id="creator-batch-message" class="muted" role="status"></p>
      <div id="creator-batches"></div>
    </section>
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
      if(["generating_script","generating_narration","rendering","sourcing_visuals","aligning_captions","generating_music","generating_images","generating_video"].includes(project.status)) {
        polling=setInterval(async () => {
          if(selected !== id) return stopPolling();
          try {
            const {project:p} = await request("GET", "/api/creator/projects/" + id);
            if(!["generating_script","generating_narration","rendering","sourcing_visuals","aligning_captions","generating_music","generating_images","generating_video"].includes(p.status)) {
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
    generate.disabled = ["generating_script","generating_narration","rendering","sourcing_visuals","aligning_captions","generating_music","generating_images"].includes(project.status);
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
    remove.disabled = ["generating_script","generating_narration","rendering","sourcing_visuals","aligning_captions","generating_music","generating_images"].includes(project.status);
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
    const paceLabel=document.createElement("label");
    paceLabel.textContent="Speaking pace ";
    const paceSelect=document.createElement("select");
    paceSelect.setAttribute("aria-label","Narration speaking pace");
    for(const [value,label] of [["0.8","Relaxed 0.8×"],["1","Normal 1×"],
                                ["1.2","Energetic 1.2×"]]){
      const option=document.createElement("option");
      option.value=value;option.textContent=label;paceSelect.append(option);
    }
    paceSelect.value="1";
    paceLabel.append(paceSelect);
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
      paceSelect.disabled=!["kokoro","edge"].includes(chosen.name);
      if(paceSelect.disabled)paceSelect.value="1";
      const voiceBusy=["generating_script","generating_narration","rendering","sourcing_visuals","aligning_captions","generating_music","generating_images"].includes(project.status);
      voiceButton.disabled=!chosen.ready||voiceBusy;
      previewVoiceButton.disabled=!chosen.ready||voiceBusy;
    }
    engineSelect.addEventListener("change", syncVoiceOptions);
    const previewVoiceButton=document.createElement("button");
    previewVoiceButton.type="button";
    previewVoiceButton.className="secondary";
    previewVoiceButton.textContent="Preview selected voice";
    previewVoiceButton.disabled=true;
    const previewVoiceAudio=document.createElement("audio");
    previewVoiceAudio.controls=true;
    previewVoiceAudio.preload="none";
    previewVoiceAudio.style.display="none";
    const previewVoiceNote=document.createElement("p");
    previewVoiceNote.className="muted";
    let previewVoiceUrl=null;
    async function previewCurrentVoice(){
      previewVoiceButton.disabled=true;
      previewVoiceNote.textContent="Generating a short voice sample...";
      try{
        const response=await fetch("/api/creator/narration/preview",{
          method:"POST",headers:{"Content-Type":"application/json"},
          body:JSON.stringify({engine:engineSelect.value,voice:voiceSelect.value,
            pace:Number(paceSelect.value)})
        });
        if(!response.ok){
          const error=await response.json().catch(()=>({}));
          throw Error(error.error||"Voice preview is unavailable.");
        }
        const blob=await response.blob();
        if(selected!==project.id)return;
        if(previewVoiceUrl)URL.revokeObjectURL(previewVoiceUrl);
        previewVoiceUrl=URL.createObjectURL(blob);
        previewVoiceAudio.src=previewVoiceUrl;
        previewVoiceAudio.style.display="";
        previewVoiceNote.textContent="Preview only; your saved narration is unchanged.";
        await previewVoiceAudio.play().catch(()=>{});
      }catch(err){
        if(selected===project.id)previewVoiceNote.textContent=err.message;
      }finally{
        if(selected===project.id)previewVoiceButton.disabled=false;
      }
    }
    previewVoiceButton.onclick=previewCurrentVoice;
    const uploadHeading=document.createElement("h5");
    uploadHeading.textContent="Or upload your own recorded voice";
    const uploadHint=document.createElement("p");
    uploadHint.className="muted";
    uploadHint.textContent="Choose one WAV recording for the hook, each scene and the closing. Audio is processed locally; uploading replaces the current narration and resets captions/export.";
    const recordingInputs=[];
    const recordingsPanel=document.createElement("div");
    if(project.script){
      const segments=[{name:"Hook"},...project.script.scenes.map((_,i)=>({name:"Scene "+(i+1)})),{name:"Closing"}];
      for(const [i,segment] of segments.entries()){
        const label=document.createElement("label");
        label.textContent=segment.name+" (WAV) ";
        const input=document.createElement("input");
        input.type="file";input.accept=".wav,audio/wav";
        label.append(input);recordingsPanel.append(label);
        recordingInputs.push(input);
      }
    }
    const uploadButton=document.createElement("button");
    uploadButton.type="button";
    uploadButton.className="secondary";
    uploadButton.textContent="Use recorded narration";
    uploadButton.disabled=!recordingInputs.length||
      ["generating_script","generating_narration","rendering","sourcing_visuals","aligning_captions","generating_music","generating_images"].includes(project.status);
    uploadButton.onclick=async()=>{
      if(recordingInputs.some(input=>!input.files?.length)){
        say("Choose one WAV recording for every script part.");return;
      }
      if(project.stages.narration?.status==="done"&&!confirm("Replace your current narration with these recordings?"))return;
      const data=new FormData();
      recordingInputs.forEach((input,i)=>data.append("part_"+i,input.files[0]));
      uploadButton.disabled=true;
      try{
        const response=await fetch("/api/creator/projects/"+project.id+"/narration/upload",{
          method:"POST",body:data
        });
        const result=await response.json().catch(()=>({}));
        if(!response.ok)throw Error(result.error||"Recorded narration upload failed.");
        say("Recorded narration imported.");
        openProject(project.id);
      }catch(err){say(err.message);uploadButton.disabled=false;}
    };
    const audioList = document.createElement("div");
    voiceBox.append(voiceTitle, voiceInfo, engineLabel, voiceLabel, previewVoiceButton,
      previewVoiceAudio,previewVoiceNote,paceLabel,voiceButton,
      uploadHeading,uploadHint,recordingsPanel,uploadButton,audioList);
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
        const selection = {engine:engineSelect.value,voice:voiceSelect.value,
          pace:Number(paceSelect.value)};
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
    visualHint.textContent = "Provide your own clips, find free stock footage automatically, or generate local AI image scenes with cinematic motion. No paid video generation required.";
    const visualList = document.createElement("div");
    visualBox.append(visualTitle, visualHint, visualList);
    const stockPanel = document.createElement("section");
    stockPanel.className = "card";
    const stockTitle = document.createElement("h4");
    stockTitle.textContent = "Find free footage automatically";
    const stockDescription = document.createElement("p");
    stockDescription.className = "muted";
    stockDescription.textContent =
      "Searches Pixabay, Pexels and Coverr using configured free API keys. Fills up to six missing scenes per request; existing footage is preserved. Review matches and usage rights before publishing.";
    const stockButton = document.createElement("button");
    stockButton.type = "button";
    stockButton.textContent = "Find footage for missing scenes";
    stockButton.disabled = ["generating_script","generating_narration","rendering","sourcing_visuals","aligning_captions","generating_music","generating_images"].includes(project.status);
    const stockStatus = document.createElement("p");
    stockStatus.className = "muted";
    stockPanel.append(stockTitle,stockDescription,stockButton,stockStatus);
    visualBox.append(stockPanel);
    if(project.status === "sourcing_visuals") stockStatus.textContent =
      "Finding and downloading free footage. Refresh this project to see the results.";
    else {
      request("GET","/api/creator/projects/" + project.id + "/stock").then(data => {
        if(selected !== project.id || !data.report) return;
        const applied = Object.keys(data.report.applied || {}).length;
        const errors = Object.keys(data.report.errors || {}).length;
        stockStatus.textContent = "Last run: " + applied + " scene(s) filled; " + errors +
          " unavailable. You can upload footage manually for any missing scenes.";
      }).catch(()=>{});
    }
    stockButton.onclick = async () => {
      if(!confirm("Search Pixabay and download up to six free stock clips for missing scenes? Footage may need manual review for relevance and licence requirements.")) return;
      stockButton.disabled=true;
      try {
        const outcome = await request("POST",
          "/api/creator/projects/" + project.id + "/stock/auto-fill",
          {approve_download:true});
        say("Stock search started for " + outcome.requested.length + " missing scenes.");
        await openProject(project.id);
      } catch(err) {say(err.message);stockButton.disabled=false;}
    };
    const finishBox = document.createElement("section");
    finishBox.className = "card";
    const finishTitle = document.createElement("h4");
    finishTitle.textContent = "Animated subtitles and video transitions";
    const timingNote = document.createElement("p");
    timingNote.className = "muted";
    timingNote.textContent = "Caption times are estimated from narration segments, not word-aligned. Fades apply to the start and end of the video; scene cuts remain unchanged.";
    const subtitleChoice = document.createElement("input");
    subtitleChoice.type = "checkbox";
    subtitleChoice.checked = true;
    const subLabel = document.createElement("label");
    subLabel.append(subtitleChoice, document.createTextNode(" Burn subtitles into final video"));
    const fadeChoice = document.createElement("input");
    fadeChoice.type = "checkbox";
    fadeChoice.checked = true;
    const fadeLabel = document.createElement("label");
    fadeLabel.append(fadeChoice, document.createTextNode(" Intro and outro fade"));
    const transitionLabel = document.createElement("label");
    transitionLabel.textContent = "Between-scene transition ";
    const transitionSelect = document.createElement("select");
    for (const [value, label] of [["cut","Hard cut"],["fade","Crossfade"],["slideleft","Slide left"],
      ["slideright","Slide right"],["zoomin","Zoom in"]]) {
      const option = document.createElement("option");
      option.value = value; option.textContent = label;
      transitionSelect.append(option);
    }
    transitionLabel.append(transitionSelect);
    const animationLabel = document.createElement("label");
    animationLabel.textContent = "Caption animation ";
    const animationSelect = document.createElement("select");
    for (const [value,label] of [["none","None"],["pop_spring","Spring pop (MoneyPrinterTurbo style)"],["fade","Fade in"]]) {
      const option = document.createElement("option");
      option.value = value; option.textContent = label;
      animationSelect.append(option);
    }
    animationLabel.append(animationSelect);
    const positionLabel = document.createElement("label");
    positionLabel.textContent = "Caption position ";
    const positionSelect = document.createElement("select");
    for (const value of ["bottom","middle","top"]) {
      const option = document.createElement("option");
      option.value = value; option.textContent = value;
      positionSelect.append(option);
    }
    positionLabel.append(positionSelect);
    const sizeLabel = document.createElement("label");
    sizeLabel.textContent = "Caption size (% of video height) ";
    const sizeInput = document.createElement("input");
    sizeInput.type = "number"; sizeInput.min = "2"; sizeInput.max = "12"; sizeInput.step = "0.5";
    sizeInput.value = "5.5";
    sizeLabel.append(sizeInput);
    const textColorLabel = document.createElement("label");
    textColorLabel.textContent = "Text colour ";
    const textColorInput = document.createElement("input");
    textColorInput.type = "color"; textColorInput.value = "#ffffff";
    textColorLabel.append(textColorInput);
    const highlightColorLabel = document.createElement("label");
    highlightColorLabel.textContent = "Highlight colour ";
    const highlightColorInput = document.createElement("input");
    highlightColorInput.type = "color"; highlightColorInput.value = "#ffd400";
    highlightColorLabel.append(highlightColorInput);
    const highlightChoice = document.createElement("input");
    highlightChoice.type = "checkbox"; highlightChoice.checked = false;
    const highlightLabel = document.createElement("label");
    highlightLabel.append(highlightChoice, document.createTextNode(" Highlight currently spoken word"));
    const saveFinish = document.createElement("button");
    saveFinish.type = "button";
    saveFinish.className = "secondary";
    saveFinish.textContent = "Save finishing options";
    saveFinish.disabled = ["generating_script","generating_narration","rendering","sourcing_visuals","aligning_captions","generating_music","generating_images"].includes(project.status);
    const finishState = document.createElement("span");
    const srt = document.createElement("a");
    srt.href = "/api/creator/projects/" + project.id + "/captions.srt";
    srt.textContent = "Download approximate SRT captions";
    srt.setAttribute("download", "creator_" + project.id + ".srt");
    finishBox.append(finishTitle,timingNote,subLabel,fadeLabel,transitionLabel,animationLabel,positionLabel,sizeLabel,textColorLabel,highlightColorLabel,highlightLabel,saveFinish,finishState,document.createTextNode(" "),srt);
    const alignButton = document.createElement("button");
    alignButton.type = "button";
    alignButton.className = "secondary";
    alignButton.textContent = "Align captions using local Whisper";
    alignButton.disabled = !["done"].includes(project.stages.narration?.status) ||
      ["generating_script","generating_narration","rendering","sourcing_visuals","aligning_captions","generating_music","generating_images"].includes(project.status);
    alignButton.onclick = async () => {
      if(!confirm("Download/use free local Whisper tiny.en model if needed? Caption alignment can take CPU time.")) return;
      alignButton.disabled = true;
      try {
        await request("POST", "/api/creator/projects/" + project.id + "/captions/align");
        say("Whisper is aligning spoken words to your narration locally.");
        openProject(project.id);
      } catch(err) {say(err.message);alignButton.disabled=false;}
    };
    finishBox.append(alignButton);
    if(project.status === "aligning_captions") timingNote.textContent =
      "Whisper is measuring word timings. Reload this project when alignment finishes.";

    visualBox.append(finishBox);
    request("GET", "/api/creator/projects/" + project.id + "/captions/options").then(result => {
      if(selected !== project.id) return;
      subtitleChoice.checked = result.settings.captions;
      fadeChoice.checked = result.settings.fade_edges;
      transitionSelect.value = result.settings.transition || "cut";
      animationSelect.value = result.settings.animation || "none";
      positionSelect.value = result.settings.position || "bottom";
      sizeInput.value = result.settings.size_pct ?? 5.5;
      textColorInput.value = result.settings.text_color || "#FFFFFF";
      highlightColorInput.value = result.settings.highlight_color || "#FFD400";
      highlightChoice.checked = Boolean(result.settings.highlight_word);
    }).catch(err => {if(selected===project.id) finishState.textContent=err.message;});
    saveFinish.onclick = async () => {
      saveFinish.disabled = true;
      try {
        await request("PUT", "/api/creator/projects/" + project.id + "/captions/options",
          {captions:subtitleChoice.checked,fade_edges:fadeChoice.checked,transition:transitionSelect.value,
           animation:animationSelect.value,position:positionSelect.value,
           size_pct:Number(sizeInput.value),text_color:textColorInput.value,
           highlight_color:highlightColorInput.value,highlight_word:highlightChoice.checked});
        say("Finishing preferences saved. Your next export will use these settings.");
        openProject(project.id);
      } catch(err) {say(err.message);saveFinish.disabled=false;}
    };
    const musicRow = document.createElement("div");
    musicRow.className = "card";
    const musicTitle = document.createElement("h4");
    musicTitle.textContent = "Background music · local file";
    const musicNote = document.createElement("p");
    musicNote.className = "muted";
    musicNote.textContent = "Optional MP3, WAV, FLAC or AAC, mixed at low volume by the upstream renderer. Use audio you have rights to.";
    const musicFile = document.createElement("input");
    musicFile.type = "file"; musicFile.accept = ".mp3,.wav,.flac,.aac,audio/*";
    const musicUpload = document.createElement("button");
    musicUpload.type = "button"; musicUpload.textContent = "Use this music";
    musicUpload.disabled = ["generating_script","generating_narration","rendering","sourcing_visuals","aligning_captions","generating_music","generating_images"].includes(project.status);
    const musicRemove = document.createElement("button");
    musicRemove.type = "button"; musicRemove.className = "secondary";
    musicRemove.textContent = "Remove music";
    musicRemove.disabled = musicUpload.disabled;
    const musicState = document.createElement("p");
    musicState.className = "muted";
    musicRow.append(musicTitle,musicNote,musicFile,musicUpload,musicRemove,musicState);
    visualBox.append(musicRow);
    request("GET", "/api/creator/projects/" + project.id + "/music").then(data => {
      if(selected === project.id) musicState.textContent = data.music
        ? "Saved: " + data.music.filename + " (" + data.music.duration + " seconds)"
        : "No background music selected.";
    }).catch(() => {});
    musicUpload.onclick = async () => {
      if(!musicFile.files.length) return say("Choose a music file first.");
      if(musicFile.files[0].size > 30*1024*1024) return say("Music must be under 30MB.");
      musicUpload.disabled=true;
      try {
        const form = new FormData(); form.append("audio", musicFile.files[0]);
        const response = await fetch("/api/creator/projects/" + project.id + "/music",
          {method:"POST",body:form});
        const result = await response.json();
        if(!response.ok) throw Error(result.error || "Upload failed.");
        say("Local music saved. It will be included in the next render.");
        openProject(project.id);
      } catch(err) {say(err.message);musicUpload.disabled=false;}
    };
    musicRemove.onclick = async () => {
      musicRemove.disabled=true;
      try {
        await request("DELETE","/api/creator/projects/" + project.id + "/music");
        say("Background music removed from future renders.");
        openProject(project.id);
      } catch(err) {say(err.message);musicRemove.disabled=false;}
    };
    const presetPanel = document.createElement("section");
    presetPanel.className = "card";
    const presetTitle = document.createElement("h4");
    presetTitle.textContent = "Free music presets and original instrumentals";
    const moodSelect = document.createElement("select");
    for(const mood of ["calm","uplifting","cinematic","ambient"]) {
      const option=document.createElement("option");
      option.value=mood;option.textContent=mood;
      moodSelect.append(option);
    }
    const generateMusic = document.createElement("button");
    generateMusic.type="button";generateMusic.className="secondary";
    generateMusic.textContent="Generate original instrumental";
    generateMusic.disabled=["generating_script","generating_narration","rendering","sourcing_visuals","aligning_captions","generating_music","generating_images"].includes(project.status);
    const presetSelect = document.createElement("select");
    const usePreset = document.createElement("button");
    usePreset.type="button";usePreset.className="secondary";usePreset.textContent="Use saved preset";
    const savePreset = document.createElement("button");
    savePreset.type="button";savePreset.className="secondary";savePreset.textContent="Save current track as preset";
    const presetInfo = document.createElement("p");
    presetInfo.className="muted";
    presetInfo.textContent="Locally synthesized background pads (not cloud AI music). You can also reuse any music you upload.";
    presetPanel.append(presetTitle,presetInfo,moodSelect,generateMusic,presetSelect,usePreset,savePreset);
    musicRow.append(presetPanel);
    async function refreshPresets() {
      try {
        const data = await request("GET","/api/creator/music/presets");
        if(selected!==project.id) return;
        presetSelect.replaceChildren();
        for(const name of ["random",...(data.saved||[])]) {
          const opt=document.createElement("option");
          opt.value=name;opt.textContent=name==="random"?"Random saved track":name;
          presetSelect.append(opt);
        }
      }catch(err){presetInfo.textContent=err.message;}
    }
    refreshPresets();
    generateMusic.onclick=async()=>{
      generateMusic.disabled=true;
      try {
        await request("POST","/api/creator/projects/"+project.id+"/music/generate",
          {mood:moodSelect.value,seconds:30});
        say("Free instrumental created. It is used on the next render.");
        openProject(project.id);
      }catch(err){say(err.message);generateMusic.disabled=false;}
    };
    savePreset.onclick=async()=>{
      savePreset.disabled=true;
      try {
        await request("POST","/api/creator/projects/"+project.id+"/music/save-preset");
        say("Track saved to reusable music library.");
        await refreshPresets();
      }catch(err){say(err.message);}
      savePreset.disabled=false;
    };
    usePreset.onclick=async()=>{
      usePreset.disabled=true;
      try {
        await request("POST","/api/creator/projects/"+project.id+"/music/select-preset",
          {filename:presetSelect.value});
        say("Music preset selected.");
        openProject(project.id);
      }catch(err){say(err.message);usePreset.disabled=false;}
    };
    const neuralPanel=document.createElement("section");
    neuralPanel.className="card";
    const neuralTitle=document.createElement("h4");
    neuralTitle.textContent="Neural AI soundtrack · local MusicGen Small";
    const neuralExplain=document.createElement("p");
    neuralExplain.className="muted";
    neuralExplain.textContent="Generate actual AI-composed instrumental audio from a text prompt. Defaults to a video-aware mood prompt and matches the narration duration. The local model creates an 8-second neural sample and crossfades its repetitions for longer videos; this is not full-length unique composition.";
    const neuralMood=document.createElement("select");
    for(const mood of ["cinematic","calm","uplifting","suspenseful","energetic","ambient"]){
      const opt=document.createElement("option");opt.value=mood;opt.textContent=mood;
      neuralMood.append(opt);
    }
    const neuralMoodLabel=document.createElement("label");
    neuralMoodLabel.textContent="Soundtrack mood ";
    neuralMoodLabel.append(neuralMood);
    const neuralLengthLabel=document.createElement("label");
    neuralLengthLabel.textContent="Soundtrack length ";
    const neuralLength=document.createElement("select");
    for(const [value,label] of [["match","Match narration (recommended)"],["15","15 seconds"],
      ["30","30 seconds"],["60","60 seconds"],["90","90 seconds"],["120","120 seconds"],
      ["180","180 seconds"]]){
      const opt=document.createElement("option");opt.value=value;opt.textContent=label;
      neuralLength.append(opt);
    }
    neuralLengthLabel.append(neuralLength);
    const neuralSampleLabel=document.createElement("label");
    neuralSampleLabel.textContent="Neural composition length ";
    const neuralSample=document.createElement("select");
    for(const [value,label] of [["8","8 seconds · fast"],["12","12 seconds · balanced"],
      ["16","16 seconds · longer composition"],["20","20 seconds · more variation, slower"]]){
      const opt=document.createElement("option");
      opt.value=value;opt.textContent=label;
      neuralSample.append(opt);
    }
    neuralSample.value="12";
    neuralSampleLabel.append(neuralSample);
    const neuralPromptLabel=document.createElement("label");
    neuralPromptLabel.textContent="Music prompt (optional; blank = match this video's topic and scenes)";
    const neuralPrompt=document.createElement("textarea");
    neuralPrompt.rows=3;
    neuralPrompt.maxLength=500;
    neuralPrompt.placeholder="e.g. Melancholic dream-pop instrumental with soft drums, shimmer guitars, no singing";
    neuralPromptLabel.append(neuralPrompt);
    const downloadLabel=document.createElement("label");
    const downloadConsent=document.createElement("input");
    downloadConsent.type="checkbox";
    downloadLabel.append(downloadConsent,document.createTextNode(" Allow free first-use MusicGen model download from Hugging Face (large download)"));
    const previewNeural=document.createElement("button");
    previewNeural.type="button";previewNeural.className="secondary";
    previewNeural.textContent="Preview music prompt";
    const generateNeural=document.createElement("button");
    generateNeural.type="button";
    generateNeural.textContent="Generate neural soundtrack locally";
    generateNeural.disabled=["generating_script","generating_narration","rendering",
      "sourcing_visuals","aligning_captions","generating_music","generating_images"].includes(project.status);
    const neuralStatus=document.createElement("p");
    neuralStatus.className="muted";
    neuralPanel.append(neuralTitle,neuralExplain,neuralMoodLabel,neuralLengthLabel,neuralSampleLabel,
      neuralPromptLabel,downloadLabel,previewNeural,generateNeural,neuralStatus);
    musicRow.append(neuralPanel);
    const listenNeural = document.createElement("audio");
    listenNeural.controls = true;
    listenNeural.preload = "none";
    listenNeural.src = "/api/creator/projects/"+project.id+"/music/neural/audio";
    listenNeural.setAttribute("aria-label","Preview neural soundtrack");
    listenNeural.hidden = true;
    neuralPanel.append(listenNeural);
    request("GET","/api/creator/projects/"+project.id+"/music").then(data => {
      if(selected !== project.id) return;
      listenNeural.hidden = data.music?.mode !== "musicgen";
    }).catch(()=>{});
    function neuralPayload(){
      return {mood:neuralMood.value,prompt:neuralPrompt.value,
        duration:neuralLength.value==="match"?"match":Number(neuralLength.value),
        sample_seconds:Number(neuralSample.value),download_model:downloadConsent.checked};
    }
    previewNeural.onclick=async()=>{
      try{
        const data=await request("POST",
          "/api/creator/projects/"+project.id+"/music/neural/preview",neuralPayload());
        neuralStatus.textContent="Prompt: "+data.prompt+
          " | Soundtrack: "+data.duration+"s (neural composition: "+data.sample_seconds+"s)";
      }catch(err){neuralStatus.textContent=err.message;}
    };
    generateNeural.onclick=async()=>{
      if(!confirm("Run free local MusicGen Small on this machine? It can use substantial RAM/VRAM, disk space and CPU time.")) return;
      generateNeural.disabled=true;
      try{
        const outcome=await request("POST",
          "/api/creator/projects/"+project.id+"/music/neural/generate",neuralPayload());
        say("Local MusicGen started for "+outcome.duration+" seconds of soundtrack.");
        openProject(project.id);
      }catch(err){neuralStatus.textContent=err.message;generateNeural.disabled=false;}
    };
    request("GET","/api/creator/music/neural/capabilities").then(info=>{
      if(selected!==project.id) return;
      if(!info.installed){
        neuralStatus.textContent="Optional model dependencies are not installed. See docs/free-musicgen.md. Other music features still work.";
      }
    }).catch(()=>{});
    request("GET","/api/creator/projects/"+project.id+"/music/neural").then(data=>{
      if(selected!==project.id) return;
      if(data.status==="running"||project.status==="generating_music")
        neuralStatus.textContent="Local MusicGen is generating. This may use substantial CPU/GPU time; the project will refresh on completion.";
      else if(data.status==="failed")
        neuralStatus.textContent="Last generation failed: "+(data.report?.error||"Check local logs")+". Your previous music remains unchanged.";
      else if(data.status==="done")
        neuralStatus.textContent="Last neural soundtrack generated: "+data.report.duration+"s using "+data.report.device+
          ". "+(data.report.looped?"Short neural motif is crossfaded to fit the video.":"");
    }).catch(()=>{});
    const imagePanel=document.createElement("section");
    imagePanel.className="card";
    const imageTitle=document.createElement("h4");
    imageTitle.textContent="Generate AI image scenes locally";
    const imageDetails=document.createElement("p");
    imageDetails.className="muted";
    imageDetails.textContent="Use optional SD-Turbo on your PC to generate 512px scene images. FFmpeg turns them into cinematic moving shots. Existing uploaded/stock clips are never replaced. This is AI image animation, not text-to-video.";
    const imageConsentLabel=document.createElement("label");
    const imageConsent=document.createElement("input");
    imageConsent.type="checkbox";
    imageConsentLabel.append(imageConsent,document.createTextNode(" Allow first-time download of the local SD-Turbo image model"));
    const imageGenerate=document.createElement("button");
    imageGenerate.type="button";
    imageGenerate.className="secondary";
    imageGenerate.textContent="Generate missing scenes as AI images";
    imageGenerate.disabled=["generating_script","generating_narration","rendering",
      "sourcing_visuals","aligning_captions","generating_music","generating_images"].includes(project.status);
    const imageNote=document.createElement("p");
    imageNote.className="muted";
    imagePanel.append(imageTitle,imageDetails,imageConsentLabel,imageGenerate,imageNote);
    visualBox.append(imagePanel);
    async function refreshImages(){
      try{
        const [cap,state]=await Promise.all([
          request("GET","/api/creator/images/capabilities"),
          request("GET","/api/creator/projects/"+project.id+"/images")]);
        if(selected!==project.id)return;
        if(!cap.installed){
          imageNote.textContent="Optional image model dependencies are missing. See docs/free-images.md.";
        }else if(state.status==="running"){
          imageNote.textContent="Generating missing images on this computer. Completed: "+
            Object.keys(state.report?.generated||{}).length;
        }else if(state.status==="done"){
          imageNote.textContent="AI image scenes created. Refresh the project to view your footage.";
        }else if(state.status==="blocked"){
          imageNote.textContent="Image fallback stopped: "+
            Object.values(state.report?.errors||{})[0]+". Previous scene clips are preserved.";
        }
      }catch(err){if(selected===project.id)imageNote.textContent=err.message;}
    }
    refreshImages();
    imageGenerate.onclick=async()=>{
      if(!confirm("Generate AI images locally for missing scene footage? This can use considerable RAM, VRAM and disk space."))return;
      imageGenerate.disabled=true;
      try{
        await request("POST","/api/creator/projects/"+project.id+"/images/fill",{
          approve_model_download:imageConsent.checked
        });
        say("Local image generation started. Refresh this project for progress.");
        await refreshImages();
      }catch(err){imageNote.textContent=err.message;imageGenerate.disabled=false;}
    };
    const autoPanel = document.createElement("section");
    autoPanel.className = "card";
    const autoTitle=document.createElement("h4");
    autoTitle.textContent="Automatic topic-to-video production";
    const autoDesc=document.createElement("p");
    autoDesc.className="muted";
    autoDesc.textContent="Automatically run the video pipeline with your chosen visual source, narration, music, captions and final MP4 rendering. Review before publishing.";
    const visualModeLabel=document.createElement("label");
    visualModeLabel.textContent="Visual source ";
    const visualMode=document.createElement("select");
    for(const [value,label] of [
      ["stock","Stock footage · Pexels / Pixabay / Coverr"],
      ["ai","AI images + motion · local SD-Turbo"],
      ["neural_video","True AI video · local CogVideoX-2B (experimental)"]
    ]){
      const option=document.createElement("option");
      option.value=value;option.textContent=label;
      visualMode.append(option);
    }
    visualModeLabel.append(visualMode);
    const visualModeNote=document.createElement("p");
    visualModeNote.className="muted";
    const stockApproval=document.createElement("label");
    const autoStock=document.createElement("input");
    autoStock.type="checkbox";
    stockApproval.append(autoStock,document.createTextNode(
      " Approve free stock-footage downloads for missing scenes"));
    const autoImageDownloadLabel=document.createElement("label");
    const autoImageDownload=document.createElement("input");
    autoImageDownload.type="checkbox";
    autoImageDownloadLabel.append(autoImageDownload,document.createTextNode(
      " Allow initial local SD-Turbo model download (only required once)"));
    function applyVisualMode(){
      const ai = visualMode.value==="ai";
      const neural = visualMode.value==="neural_video";
      stockApproval.hidden=ai||neural;
      autoImageDownloadLabel.hidden=!(ai||neural);
      visualModeNote.textContent=neural
        ? "Genuine neural text-to-video: experimental 6GB preset produces 17 moving frames (~2 seconds) per scene; clips may be repeated to match narration. Quality may be poor, inference can be slow, and 6GB VRAM may still run out."
        : ai
          ? "No stock API calls. SD-Turbo creates fresh still images for every scene; FFmpeg adds camera motion. This is NOT neural text-to-video."
          : "Uses your configured Pexels, Pixabay or Coverr key to fetch stock video clips.";
      autoImageDownloadLabel.lastChild.textContent=neural
        ? " Allow first-use CogVideoX-2B model download (large, explicit opt-in)"
        : " Allow first-use SD-Turbo image model download";
      if(ai||neural)autoStock.checked=false;
    }
    visualMode.onchange=applyVisualMode;
    applyVisualMode();
    const alignLabel=document.createElement("label");
    const autoAlign=document.createElement("input");
    autoAlign.type="checkbox";
    alignLabel.append(autoAlign,document.createTextNode(" Align captions locally with Whisper when required"));
    const musicLabel=document.createElement("label");
    musicLabel.textContent="Soundtrack ";
    const autoMusic=document.createElement("select");
    for(const [value,label] of [["keep","Keep selected music / none"],
                                ["instrumental","Create free local instrumental if none selected"],
                                ["neural","Generate MusicGen neural soundtrack if none selected"],
                                ["none","No music"]]){
      const option=document.createElement("option");option.value=value;option.textContent=label;
      autoMusic.append(option);
    }
    musicLabel.append(autoMusic);
    const autoModelLabel=document.createElement("label");
    const autoModelConsent=document.createElement("input");
    autoModelConsent.type="checkbox";
    autoModelLabel.append(autoModelConsent,document.createTextNode(
      " Allow first-use download of local MusicGen weights (only if neural music is selected)"));
    const startAuto=document.createElement("button");
    startAuto.type="button";
    startAuto.textContent="Generate video automatically";
    startAuto.disabled=["generating_script","generating_narration","rendering",
      "sourcing_visuals","aligning_captions","generating_music","generating_images"].includes(project.status);
    const autoMessage=document.createElement("p");
    autoMessage.className="muted";
    const autoDownload=document.createElement("a");
    autoDownload.href="/api/creator/projects/"+project.id+"/render/file";
    autoDownload.setAttribute("download","creator_"+project.id+".mp4");
    autoDownload.textContent="Download final MP4";
    autoDownload.style.display="none";
    const previewPanel=document.createElement("div");
    previewPanel.style.display="none";
    const previewHeading=document.createElement("h4");
    previewHeading.textContent="Review finished video";
    const finalPlayer=document.createElement("video");
    finalPlayer.controls=true;
    finalPlayer.preload="metadata";
    finalPlayer.playsInline=true;
    finalPlayer.style.cssText="width:100%;max-height:480px;background:#111;border-radius:8px";
    const previewMessage=document.createElement("p");
    previewMessage.className="muted";
    previewPanel.append(previewHeading,finalPlayer,previewMessage);
    autoPanel.append(autoTitle,autoDesc,visualModeLabel,visualModeNote,stockApproval,autoImageDownloadLabel,alignLabel,musicLabel,autoModelLabel,startAuto,autoMessage,autoDownload,previewPanel);
    async function refreshExport(){
      const info=await request("GET","/api/creator/projects/"+project.id+"/render");
      if(selected!==project.id)return;
      if(info.ready){
        autoDownload.style.display="";
        previewPanel.style.display="";
        const url=info.preview_url;
        if(finalPlayer.getAttribute("src")!==url){
          finalPlayer.src=url;
          finalPlayer.load();
        }
        previewMessage.textContent="Check visuals, narration and captions before publishing.";
      }else{
        autoDownload.style.display="none";
        previewPanel.style.display="none";
        finalPlayer.removeAttribute("src");
        finalPlayer.load();
        if(info.status==="done")previewMessage.textContent="Export file missing. Re-render this video.";
      }
    }
    visualBox.append(autoPanel);
    let autoPollTimer=null;
    async function refreshAuto(){
      try{
        const info=await request("GET","/api/creator/projects/"+project.id+"/auto");
        if(selected!==project.id) return;
        const completed=info.report?.completed?.join(", ")||"none";
        autoDownload.style.display="none";
        if(info.status==="running"){
          autoMessage.textContent="Working on "+info.step+" · completed: "+completed;
          startAuto.disabled=true;
          if(!autoPollTimer){
            autoPollTimer=setTimeout(async()=>{
              autoPollTimer=null;
              if(selected===project.id) await refreshAuto();
            },3000);
          }
        }else if(info.status==="blocked"){
          autoMessage.textContent="Stopped at "+info.step+": "+(info.report?.error||"Review the job report")+
            ". Completed: "+completed+". You can retry.";
          startAuto.disabled=false;
        }else if(info.status==="done"){
          const current=await request("GET","/api/creator/projects/"+project.id);
          if(selected!==project.id)return;
          if(current.project?.stages?.render?.status==="done"){
            await refreshExport();
            autoMessage.textContent=autoDownload.style.display===""?
              "Video ready for review. Play it below or download the final MP4.":
              "Render stage completed, but the final MP4 is unavailable. Re-render the video.";
            request("GET","/api/creator/projects/"+project.id+"/visuals")
              .then(data=>{if(selected===project.id)drawSlots(data.clips||{},data.sources||{});})
              .catch(()=>{});
          }else{
            autoMessage.textContent="The automation reported completion, but no completed render is recorded. Check the render stage before downloading.";
          }
          startAuto.disabled=false;
        }else if(info.status==="interrupted"){
          autoMessage.textContent="Previous job interrupted. Restart to reuse completed stages.";
          startAuto.disabled=false;
        }
      }catch(err){if(selected===project.id) autoMessage.textContent=err.message;}
    }
    refreshAuto();
    startAuto.onclick=async()=>{
      if(!confirm("Automatically generate this video using local AI and your saved settings? The final video will require your review before publishing."))return;
      startAuto.disabled=true;
      try{
        await request("POST","/api/creator/projects/"+project.id+"/auto/start",{
          visual_mode:visualMode.value,
          approve_stock_download:visualMode.value==="stock"&&autoStock.checked,
          approve_image_model_download:visualMode.value!=="stock"&&autoImageDownload.checked,
          align_captions:autoAlign.checked,
          music:autoMusic.value,
          approve_model_download:autoModelConsent.checked
        });
        say("Automatic video production started. The job status shows each stage.");
        await refreshAuto();
      }catch(err){autoMessage.textContent=err.message;startAuto.disabled=false;}
    };
    const editPanel = document.createElement("section");
    editPanel.className = "card";
    const editTitle = document.createElement("h4");
    editTitle.textContent = "Advanced video editing";
    const editHint = document.createElement("p");
    editHint.className = "muted";
    editHint.textContent = "Changes apply to footage; narration, subtitle order and scene lengths stay intact. Crop fills the frame; letterbox shows the whole shot.";
    function selectEditing(label, values) {
      const wrapper = document.createElement("label");
      wrapper.textContent = label + " ";
      const control = document.createElement("select");
      for (const [value, display] of values) {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = display;
        control.append(option);
      }
      wrapper.append(control);
      editPanel.append(wrapper);
      return control;
    }
    editPanel.append(editTitle, editHint);
    const outputFormat = selectEditing("Output format",
      [["portrait","Portrait 9:16 (720×1280)"],["landscape","Landscape 16:9 (1280×720)"],
       ["square","Square 1:1 (1080×1080)"]]);
    const fitMode = selectEditing("Footage fitting",
      [["cover","Crop to fill"],["contain","Letterbox / show full source"]]);
    const footageSpeed = selectEditing("Footage playback speed",
      [["0.5","0.5× slow"],["0.75","0.75×"],["1","Normal 1×"],
       ["1.25","1.25×"],["1.5","1.5×"],["2","2× fast"]]);
    const footageOrder = selectEditing("Footage assignment",
      [["script","Scene order"],["reverse","Reverse footage"],["shuffle","Shuffle footage (repeatable)"]]);
    const narrationLevel=selectEditing("Narration volume",[
      ["0","Mute"],["0.5","50%"],["0.75","75%"],["1","100% (default)"],
      ["1.25","125%"],["1.5","150%"],["2","200%"]]);
    const musicLevel=selectEditing("Background music volume",[
      ["0","Mute"],["0.05","5%"],["0.1","10%"],["0.15","15% (default)"],
      ["0.25","25%"],["0.4","40%"],["0.6","60%"],["1","100%"]]);
    const duckingLabel=document.createElement("label");
    const musicDucking=document.createElement("input");
    musicDucking.type="checkbox";
    duckingLabel.append(musicDucking,document.createTextNode(
      " Automatically lower music during narration (audio ducking)"));
    editPanel.append(duckingLabel);
    const saveEdit = document.createElement("button");
    saveEdit.type = "button";
    saveEdit.className = "secondary";
    saveEdit.textContent = "Save editing controls";
    saveEdit.disabled = ["generating_script","generating_narration","rendering","sourcing_visuals","aligning_captions","generating_music","generating_images"].includes(project.status);
    const editStatus = document.createElement("p");
    editStatus.className = "muted";
    editPanel.append(saveEdit,editStatus);
    visualBox.append(editPanel);
    request("GET","/api/creator/projects/" + project.id + "/edit/options")
      .then(data => {
        if(selected !== project.id) return;
        outputFormat.value = data.options.format;
        fitMode.value = data.options.fit;
        footageSpeed.value = String(data.options.speed);
        footageOrder.value = data.options.order;
        narrationLevel.value = String(data.options.narration_volume ?? 1);
        musicLevel.value = String(data.options.bgm_volume ?? 0.15);
        musicDucking.checked = data.options.music_ducking === true;
      })
      .catch(err => {if(selected === project.id) editStatus.textContent=err.message;});
    saveEdit.onclick=async()=>{
      saveEdit.disabled=true;
      try{
        await request("PUT","/api/creator/projects/" + project.id + "/edit/options",{
          format:outputFormat.value,fit:fitMode.value,
          speed:Number(footageSpeed.value),order:footageOrder.value,
          narration_volume:Number(narrationLevel.value),
          bgm_volume:Number(musicLevel.value),
          music_ducking:musicDucking.checked
        });
        say("Editing settings saved. The next render uses the selected visual and audio mix controls.");
        openProject(project.id);
      }catch(err){say(err.message);saveEdit.disabled=false;}
    };
    const assignmentPanel=document.createElement("section");
    assignmentPanel.className="card";
    const assignmentTitle=document.createElement("h4");
    assignmentTitle.textContent="Manual scene footage assignment";
    const assignmentHint=document.createElement("p");
    assignmentHint.className="muted";
    assignmentHint.textContent="Reuse a saved scene's footage for any other scene. This changes visuals only; narration and captions stay in script order.";
    const assignmentRows=document.createElement("div");
    const assignmentSave=document.createElement("button");
    assignmentSave.type="button";
    assignmentSave.className="secondary";
    assignmentSave.textContent="Save scene assignments";
    const assignmentStatus=document.createElement("p");
    assignmentStatus.className="muted";
    assignmentPanel.append(assignmentTitle,assignmentHint,assignmentRows,assignmentSave,assignmentStatus);
    visualBox.append(assignmentPanel);
    const assignmentSlots=["hook",...script.scenes.map((_,i)=>"scene_"+i),"closing"];
    const assignmentSelects={};
    const assignmentName=slot=>slot==="hook"?"Opening":slot==="closing"?"Ending":
      "Scene "+(Number(slot.split("_")[1])+1);
    for(const target of assignmentSlots){
      const label=document.createElement("label");
      label.textContent=assignmentName(target)+" footage source ";
      const select=document.createElement("select");
      const selfOption=document.createElement("option");
      selfOption.value="";selfOption.textContent="Default assignment";
      select.append(selfOption);
      for(const source of assignmentSlots){
        const option=document.createElement("option");
        option.value=source;
        option.textContent=assignmentName(source);
        select.append(option);
      }
      assignmentSelects[target]=select;
      label.append(select);
      assignmentRows.append(label);
    }
    request("GET","/api/creator/projects/"+project.id+"/edit/scene-assignments")
      .then(({assignments})=>{
        if(selected!==project.id)return;
        for(const [target,select] of Object.entries(assignmentSelects)){
          select.value=assignments[target]||"";
        }
      }).catch(err=>{if(selected===project.id)assignmentStatus.textContent=err.message;});
    assignmentSave.onclick=async()=>{
      assignmentSave.disabled=true;
      try{
        const assignments={};
        for(const [target,select] of Object.entries(assignmentSelects)){
          if(select.value) assignments[target]=select.value;
        }
        await request("PUT","/api/creator/projects/"+project.id+"/edit/scene-assignments",
          {assignments});
        say("Scene footage assignments saved. Narration and subtitles are unchanged.");
        await openProject(project.id);
      }catch(err){assignmentStatus.textContent=err.message;assignmentSave.disabled=false;}
    };
    const effectsPanel=document.createElement("section");
    effectsPanel.className="card";
    const effectsTitle=document.createElement("h4");
    effectsTitle.textContent="Per-scene footage effects";
    const effectsHint=document.createElement("p");
    effectsHint.className="muted";
    effectsHint.textContent="Override footage speed, framing, crop focus, and in/out points for each scene. Trimming changes only visual footage; narration and subtitle timing stay unchanged.";
    effectsPanel.append(effectsTitle,effectsHint);
    const effectsRows={};
    const effectsSlots=["hook",...script.scenes.map((_,i)=>"scene_"+i),"closing"];
    for(const slot of effectsSlots){
      const name=slot==="hook"?"Opening":slot==="closing"?"Ending":
        "Scene "+(Number(slot.split("_")[1])+1);
      const wrap=document.createElement("div");
      wrap.className="card";
      const label=document.createElement("strong");
      label.textContent=name;
      const speedLabel=document.createElement("label");
      speedLabel.textContent=" Playback speed ";
      const speedSelect=document.createElement("select");
      for(const [value,text] of [
        ["","Global default"],["0.5","0.5×"],["0.75","0.75×"],
        ["1","1×"],["1.25","1.25×"],["1.5","1.5×"],["2","2×"]]){
        const option=document.createElement("option");
        option.value=value;option.textContent=text;speedSelect.append(option);
      }
      speedLabel.append(speedSelect);
      const fitLabel=document.createElement("label");
      fitLabel.textContent=" Framing ";
      const fitSelect=document.createElement("select");
      for(const [value,text] of [
        ["","Global default"],["cover","Crop to fill"],["contain","Letterbox"]]){
        const option=document.createElement("option");
        option.value=value;option.textContent=text;fitSelect.append(option);
      }
      fitLabel.append(fitSelect);
      const cropXLabel=document.createElement("label");
      cropXLabel.textContent=" Horizontal crop focus ";
      const cropXSelect=document.createElement("select");
      for(const [value,name] of [["","Centre (default)"],["0","Left"],["0.5","Centre"],["1","Right"]]){
        const option=document.createElement("option");
        option.value=value;option.textContent=name;cropXSelect.append(option);
      }
      cropXLabel.append(cropXSelect);
      const cropYLabel=document.createElement("label");
      cropYLabel.textContent=" Vertical crop focus ";
      const cropYSelect=document.createElement("select");
      for(const [value,name] of [["","Centre (default)"],["0","Top"],["0.5","Centre"],["1","Bottom"]]){
        const option=document.createElement("option");
        option.value=value;option.textContent=name;cropYSelect.append(option);
      }
      cropYLabel.append(cropYSelect);
      const trimStartLabel=document.createElement("label");
      trimStartLabel.textContent=" Clip in (seconds) ";
      const trimStartInput=document.createElement("input");
      trimStartInput.type="number";trimStartInput.min="0";trimStartInput.max="3600";
      trimStartInput.step="0.1";trimStartInput.placeholder="Beginning";
      trimStartLabel.append(trimStartInput);
      const trimEndLabel=document.createElement("label");
      trimEndLabel.textContent=" Clip out (seconds) ";
      const trimEndInput=document.createElement("input");
      trimEndInput.type="number";trimEndInput.min="0";trimEndInput.max="3600";
      trimEndInput.step="0.1";trimEndInput.placeholder="End of clip";
      trimEndLabel.append(trimEndInput);
      wrap.append(label,speedLabel,fitLabel,cropXLabel,cropYLabel,trimStartLabel,trimEndLabel);
      effectsPanel.append(wrap);
      effectsRows[slot]={speedSelect,fitSelect,cropXSelect,cropYSelect,trimStartInput,trimEndInput};
    }
    const effectsSave=document.createElement("button");
    effectsSave.type="button";effectsSave.className="secondary";
    effectsSave.textContent="Save per-scene effects";
    const effectsStatus=document.createElement("p");
    effectsStatus.className="muted";
    effectsPanel.append(effectsSave,effectsStatus);
    visualBox.append(effectsPanel);
    request("GET","/api/creator/projects/"+project.id+"/edit/scene-effects")
      .then(({effects})=>{
        if(selected!==project.id)return;
        for(const [slot,fields] of Object.entries(effectsRows)){
          fields.speedSelect.value=effects[slot]?.speed==null?"":String(effects[slot].speed);
          fields.fitSelect.value=effects[slot]?.fit||"";
          fields.cropXSelect.value=effects[slot]?.crop_x==null?"":String(effects[slot].crop_x);
          fields.cropYSelect.value=effects[slot]?.crop_y==null?"":String(effects[slot].crop_y);
          fields.trimStartInput.value=effects[slot]?.trim_start==null?"":String(effects[slot].trim_start);
          fields.trimEndInput.value=effects[slot]?.trim_end==null?"":String(effects[slot].trim_end);
        }
      }).catch(err=>{if(selected===project.id)effectsStatus.textContent=err.message;});
    effectsSave.onclick=async()=>{
      effectsSave.disabled=true;
      try{
        const effects={};
        for(const [slot,fields] of Object.entries(effectsRows)){
          const override={};
          if(fields.speedSelect.value) override.speed=Number(fields.speedSelect.value);
          if(fields.fitSelect.value) override.fit=fields.fitSelect.value;
          if(fields.cropXSelect.value!=="") override.crop_x=Number(fields.cropXSelect.value);
          if(fields.cropYSelect.value!=="") override.crop_y=Number(fields.cropYSelect.value);
          if(fields.trimStartInput.value!=="") override.trim_start=Number(fields.trimStartInput.value);
          if(fields.trimEndInput.value!=="") override.trim_end=Number(fields.trimEndInput.value);
          if(Object.keys(override).length) effects[slot]=override;
        }
        await request("PUT","/api/creator/projects/"+project.id+"/edit/scene-effects",{effects});
        say("Scene effects saved. Video timing remains aligned with narration.");
        await openProject(project.id);
      }catch(err){effectsStatus.textContent=err.message;effectsSave.disabled=false;}
    };
    const renderButton = document.createElement("button");
    renderButton.type = "button";
    renderButton.textContent = "Render complete video";
    renderButton.disabled = true;
    const renderMessage = document.createElement("p");
    renderMessage.className = "muted";
    visualBox.append(renderButton, renderMessage);
    container.append(visualBox);
    if (project.status === "rendering") {
      const cancel = document.createElement("button");
      cancel.type = "button";
      cancel.className = "secondary";
      cancel.textContent = "Cancel render";
      cancel.onclick = async () => {
        cancel.disabled = true;
        try {
          await request("POST", "/api/creator/projects/" + project.id + "/render/cancel");
          say("Cancellation requested. The current FFmpeg stage will stop.");
        } catch(err) { say(err.message); cancel.disabled=false; }
      };
      visualBox.append(cancel);
      request("GET", "/api/creator/projects/" + project.id + "/render")
        .then(data => { if (selected === project.id) renderMessage.textContent =
          data.completed_stages + " rendering stages completed. You can leave this screen running."; })
        .catch(() => {});
    }
    const slots = ["hook", ...script.scenes.map((_, index) => "scene_" + index), "closing"];
    function drawSlots(clips, sources = {}) {
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
        upload.disabled = ["generating_script","generating_narration","rendering","sourcing_visuals","aligning_captions","generating_music","generating_images"].includes(project.status);
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
        if(clips[slot]){
          const previewToggle=document.createElement("button");
          previewToggle.type="button";
          previewToggle.className="secondary";
          previewToggle.textContent="Preview scene footage";
          const previewBox=document.createElement("div");
          previewBox.style.cssText="display:none;width:100%;max-width:480px;margin-top:8px";
          const player=document.createElement("video");
          player.controls=true;
          player.preload="none";
          player.playsInline=true;
          player.style.cssText="width:100%;max-height:360px;background:#111;border-radius:8px";
          const hint=document.createElement("p");
          hint.className="muted";
          hint.textContent="Preview shows original footage and saved trim in/out times; final crop, speed, captions and transitions appear after rendering. Some MOV/MKV codecs may not play in this browser.";
          let inPoint=0,outPoint=null;
          request("GET","/api/creator/projects/"+project.id+"/edit/scene-effects")
            .then(({effects})=>{
              if(selected!==project.id)return;
              const setting=effects?.[slot]||{};
              inPoint=Number(setting.trim_start||0);
              outPoint=setting.trim_end==null?null:Number(setting.trim_end);
            }).catch(()=>{});
          player.addEventListener("loadedmetadata",()=>{
            if(inPoint>0&&inPoint<player.duration)player.currentTime=inPoint;
          });
          player.addEventListener("timeupdate",()=>{
            if(outPoint!==null&&player.currentTime>=outPoint){
              player.pause();
              player.currentTime=Math.max(0,inPoint);
            }
          });
          player.addEventListener("error",()=>{
            hint.textContent="Your browser cannot preview this clip format. The local FFmpeg renderer may still process it.";
          });
          previewToggle.onclick=()=>{
            const opening=previewBox.style.display==="none";
            previewBox.style.display=opening?"block":"none";
            previewToggle.textContent=opening?"Close footage preview":"Preview scene footage";
            if(opening){
              player.src="/api/creator/projects/"+project.id+"/visuals/"+slot+"/preview";
              player.load();
            }else{
              player.pause();
              player.removeAttribute("src");
              player.load();
            }
          };
          previewBox.append(player,hint);
          wrap.append(previewToggle,previewBox);
        }
        const source = sources[slot];
        if (source && ["pixabay","pexels","coverr"].includes(source.provider)) {
          const note = document.createElement("span");
          note.textContent = source.provider + " · " + (source.query || "stock video");
          wrap.append(note);
          if (typeof source.source_page === "string" && /^https:\/\/(?:[a-z0-9-]+\.)?(?:pixabay\.com|pexels\.com|coverr\.co)\//i.test(source.source_page)) {
            const link = document.createElement("a");
            link.href = source.source_page;
            link.target = "_blank";
            link.rel = "noopener noreferrer";
            link.textContent = "Review original";
            wrap.append(link);
          }
          const reject=document.createElement("button");
          reject.type="button";
          reject.className="secondary";
          reject.textContent="Reject this stock clip";
          reject.disabled=["generating_script","generating_narration","rendering","sourcing_visuals","aligning_captions","generating_music","generating_images"].includes(project.status);
          reject.onclick=async()=>{
            if(!confirm("Reject this stock clip and leave the scene empty for another selection?"))return;
            reject.disabled=true;
            try{
              await request("POST","/api/creator/projects/"+project.id+"/stock/reject/"+slot,{});
              say("Stock clip rejected. You can request another shot or upload your own.");
              await openProject(project.id);
            }catch(err){say(err.message);reject.disabled=false;}
          };
          wrap.append(reject);
        }
        visualList.append(wrap);
      }
      const ready = slots.every(slot => Boolean(clips[slot]));
      renderButton.disabled = !ready ||
        project.stages.narration?.status !== "done" ||
        ["generating_script","generating_narration","rendering","sourcing_visuals","aligning_captions","generating_music","generating_images"].includes(project.status);
      renderMessage.textContent = ready
        ? "All scene footage uploaded. Narration is required to render."
        : "Upload footage for each part of the script to enable rendering.";
    }
    request("GET", "/api/creator/projects/" + project.id + "/visuals")
      .then(data => { if(selected === project.id) drawSlots(data.clips || {}, data.sources || {}); })
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
      refreshExport().catch(err=>{if(selected===project.id)previewMessage.textContent=err.message;});
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
  let batchPollTimer=null;
  async function refreshBatches(){
    const list=$("creator-batches");
    try{
      const result=await request("GET","/api/creator/batches");
      list.replaceChildren();
      for(const summary of result.batches){
        const {batch}=await request("GET","/api/creator/batches/"+summary.id);
        const wrapper=document.createElement("section");
        wrapper.className="card";
        const heading=document.createElement("strong");
        heading.textContent="Batch "+batch.id+" · "+batch.status;
        wrapper.append(heading);
        for(const item of batch.items){
          const row=document.createElement("div");
          row.style.cssText="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:8px";
          const open=document.createElement("button");
          open.type="button";open.className="secondary";
          open.textContent=item.topic+" · "+item.status+
            (item.status==="running"&&item.current_step?" ("+item.current_step+")":"");
          open.onclick=()=>openProject(item.project_id);
          row.append(open);
          if(item.status==="done"){
            const download=document.createElement("a");
            download.href="/api/creator/projects/"+item.project_id+"/render/file";
            download.setAttribute("download","creator_"+item.project_id+".mp4");
            download.textContent="Download MP4";
            row.append(download);
          }
          if(item.error){
            const detail=document.createElement("span");
            detail.className="muted";
            detail.textContent=item.error;
            row.append(detail);
          }
          wrapper.append(row);
        }
        if(batch.status==="running"){
          const pause=document.createElement("button");
          pause.type="button";pause.className="secondary";
          pause.textContent="Pause after current video";
          pause.onclick=async()=>{
            pause.disabled=true;
            try{
              await request("POST","/api/creator/batches/"+batch.id+"/pause",{});
              await refreshBatches();
            }catch(err){$("creator-batch-message").textContent=err.message;pause.disabled=false;}
          };
          wrapper.append(pause);
        }
        if(batch.status==="paused"){
          const note=document.createElement("p");
          note.className="muted";
          note.textContent="Paused: the active video may still be finishing. Completed videos are preserved.";
          wrapper.append(note);
        }
        if(batch.status!=="running" && batch.items.some(x=>["blocked","interrupted","queued"].includes(x.status))){
          const retry=document.createElement("button");
          retry.type="button";retry.className="secondary";
          retry.textContent=batch.status==="paused"?"Resume remaining videos":"Retry incomplete videos";
          retry.onclick=async()=>{
            retry.disabled=true;
            try{
              await request("POST","/api/creator/batches/"+batch.id+"/retry",{});
              await refreshBatches();
            }catch(err){$("creator-batch-message").textContent=err.message;retry.disabled=false;}
          };
          wrapper.append(retry);
        }
        list.append(wrapper);
      }
      // Keep progress visible without manual refresh, but avoid polling
      // indefinitely once every batch has finished.
      if(batchPollTimer){clearTimeout(batchPollTimer);batchPollTimer=null;}
      if(result.batches.some(b=>b.status==="running")){
        batchPollTimer=setTimeout(()=>{batchPollTimer=null;refreshBatches();},5000);
      }
    }catch(err){$("creator-batch-message").textContent=err.message;}
  }
  $("creator-batch-start").onclick=async()=>{
    const button=$("creator-batch-start");
    const topics=$("creator-batch-topics").value.split(/\r?\n/).map(t=>t.trim()).filter(Boolean);
    if(topics.length<2||topics.length>8){
      $("creator-batch-message").textContent="Enter 2–8 topics, one per line.";
      return;
    }
    if(!confirm("Create "+topics.length+" independent videos? They will generate one at a time and require review before publishing."))return;
    button.disabled=true;
    const mode=$("creator-batch-mode").value;
    const approved=$("creator-batch-download").checked;
    try{
      await request("POST","/api/creator/batches",{
        topics,
        audience:$("creator-audience").value,
        target_seconds:Number($("creator-length").value),
        options:{
          visual_mode:mode,
          approve_stock_download:mode==="stock"&&approved,
          approve_image_model_download:mode!=="stock"&&approved,
          music:$("creator-batch-music").value,
          approve_model_download:false,
          align_captions:false
        }
      });
      $("creator-batch-message").textContent="Batch started. Open its projects to inspect stages.";
      await refreshList();
      await refreshBatches();
    }catch(err){$("creator-batch-message").textContent=err.message;}
    finally{button.disabled=false;}
  };
  $("creator-batch-refresh").onclick=refreshBatches;
  $("creator-refresh").onclick=refreshList;
  refreshBatches();
  refreshList();
})();
