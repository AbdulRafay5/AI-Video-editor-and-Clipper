
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
    Narration, visuals and rendering will be added as separate production stages.</p>
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
      if(project.status === "generating_script") {
        polling=setInterval(async () => {
          if(selected !== id) return stopPolling();
          try {
            const {project:p} = await request("GET", "/api/creator/projects/" + id);
            if(p.status !== "generating_script") {
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
    generate.disabled = project.status === "generating_script";
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
    remove.disabled = project.status === "generating_script";
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
    const script = project.script;
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
