// Optional original video generation. No network submission until user approval.
(() => {
  const panel = document.getElementById("project-panel");
  if (!panel) return;
  const section = document.createElement("section");
  section.className = "card";
  section.innerHTML = [
    "<h3>Generate an original AI video scene</h3>",
    "<p class=\"muted\">This optional external service may charge for each scene. Your free editor remains unchanged.</p>",
    "<label>Describe the scene<textarea id=\"scene-prompt\" rows=\"3\" maxlength=\"1200\" placeholder=\"Documentary-style footage of a coffee shop counter, natural sunlight, realistic movement\"></textarea></label>",
    "<div class=\"actions\"><label>Duration <select id=\"scene-duration\"><option value=\"4\">4s</option><option value=\"6\">6s</option><option value=\"8\">8s</option><option value=\"10\">10s</option><option value=\"15\">15s</option></select></label>",
    "<label>Shape <select id=\"scene-shape\"><option value=\"9:16\">Vertical</option><option value=\"16:9\">Horizontal</option><option value=\"1:1\">Square</option></select></label></div>",
    "<p id=\"scene-availability\" class=\"muted\"></p>",
    "<label class=\"check\"><input type=\"checkbox\" id=\"scene-consent\">I checked the provider price and approve ONE potentially paid generation.</label>",
    "<button type=\"button\" id=\"scene-generate\">Generate one scene</button>",
    "<p id=\"scene-message\" role=\"status\" class=\"msg\"></p><div id=\"scene-output\"></div>"
  ].join("");
  panel.append(section);
  const $ = id => document.getElementById(id);
  const say = t => { $("scene-message").textContent = t; };
  let active = false;
  (async () => {
    try {
      const r = await fetch("/api/video-generation/capabilities");
      const data = await r.json();
      $("scene-availability").textContent = data.configured
        ? "Video generation configured. Check provider pricing before submitting."
        : "Optional paid generator is OFF. Set WAVESPEED_API_KEY to enable it. All free editor features still work.";
    } catch (_) { $("scene-availability").textContent = "Generator status unavailable."; }
  })();
  $("scene-generate").addEventListener("click", async () => {
    if (active) return;
    if (!$("scene-consent").checked) { say("Review the current provider price and approve one paid generation first."); return; }
    const prompt = $("scene-prompt").value.trim();
    if (prompt.length < 12) { say("Write a more detailed scene description."); return; }
    if (!confirm("Submit ONE potentially paid video-generation request? Check the current price first.")) return;
    active = true;
    $("scene-generate").disabled = true;
    $("scene-consent").checked = false;
    $("scene-output").replaceChildren();
    say("Submitting one generation request...");
    try {
      const r = await fetch("/api/video-generation/jobs", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({prompt, duration: Number($("scene-duration").value),
          aspect_ratio: $("scene-shape").value, approve_paid_generation: true})
      });
      const job = await r.json();
      if (!r.ok) throw Error(job.error || "Generation submission failed.");
      say("Submitted job " + job.provider_job_id + ". Checking progress...");
      for (let attempt = 0; attempt < 120; attempt++) {
        await new Promise(resolve => setTimeout(resolve, 5000));
        const poll = await fetch("/api/video-generation/jobs/" + job.job_id);
        const result = await poll.json();
        if (!poll.ok) throw Error(result.error || "Status unavailable. Check provider dashboard.");
        say("Generation status: " + result.status + " (job " + result.provider_job_id + ")");
        if (["failed", "cancelled", "timeout"].includes(result.status)) break;
        if (result.status === "completed") {
          if (!result.outputs.length) say("Completed without a downloadable video; check provider dashboard.");
          for (const url of result.outputs) {
            const link = document.createElement("a");
            link.href = url; link.target = "_blank"; link.rel = "noopener noreferrer";
            link.textContent = "Open generated video";
            $("scene-output").append(link, document.createTextNode(" "));
          }
          break;
        }
      }
    } catch (err) { say(err.message + " Check provider dashboard before trying again: the earlier request may have been charged."); }
    finally { active = false; $("scene-generate").disabled = false; }
  });
})();