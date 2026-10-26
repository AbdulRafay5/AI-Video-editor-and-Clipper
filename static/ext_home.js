// Persona-style home: clip count, campaign rules and project cards, using your real data.
// Auto-loaded (static/ext_*.js). Uses helpers/globals from app.js ($, api, el, fmtTime, selectProject).
(() => {
  const css = `
    .p3-hero{display:grid;gap:40px;grid-template-columns:1.15fr 1fr;align-items:start}
    @media (max-width:820px){.p3-hero{grid-template-columns:1fr}}
    .p3-label{display:inline-block;margin:0;padding:6px 34px 6px 16px;background:var(--fg);color:var(--bg);font-size:1.1rem;
      font-family:var(--display);font-style:italic;text-transform:uppercase;clip-path:polygon(0 0,100% 0,92% 100%,0 100%)}
    .p3-numwrap{position:relative;margin-top:10px}
    .p3-big{display:block;font-family:var(--display);font-style:italic;font-size:clamp(8rem,24vw,16rem);line-height:.8;
      color:transparent;-webkit-text-stroke:4px var(--fg);transform:skewX(-8deg);padding:.15em .1em 0}
    .p3-new{position:absolute;left:0;top:-4px;background:var(--yellow);color:#000;font-family:var(--display);font-style:italic;
      text-transform:uppercase;font-size:clamp(2.4rem,7vw,4.6rem);line-height:1;padding:.05em .45em .12em;
      transform:rotate(-7deg) skewX(-10deg);clip-path:polygon(2% 0,100% 7%,97% 100%,0 91%);text-shadow:3px 3px 0 var(--accent);
      animation:p3pop .5s cubic-bezier(.2,1.6,.4,1) both .3s}
    @keyframes p3pop{from{transform:rotate(-7deg) skewX(-10deg) scale(.3);opacity:0}to{transform:rotate(-7deg) skewX(-10deg) scale(1);opacity:1}}
    .p3-sub{margin:18px 0 0;max-width:30rem;font-size:1.15rem}
    .p3-strike{position:relative;display:inline-block}
    .p3-strike::after{content:"";position:absolute;left:-4%;right:-4%;top:54%;height:.22em;background:var(--yellow);transform:rotate(-3deg);opacity:.9}
    .p3-actions{display:flex;flex-wrap:wrap;gap:22px;margin-top:26px}
    .p3-actions button>span{display:inline-block;transform:skewX(12deg);transition:transform .1s}
    .p3-actions button:hover>span{transform:skewX(20deg)}
    .p3-actions button{font-size:1.05rem;padding:11px 26px;box-shadow:6px 6px 0 var(--shadow)}
    .p3-key{display:inline-grid;place-items:center;width:1.5em;height:1.5em;margin-left:10px;border:2px solid currentColor;border-radius:50%;font-style:normal;font-size:.8em}
    .p3-cw{position:relative;filter:drop-shadow(8px 8px 0 var(--shadow));transition:transform .12s}
    .p3-card{background:var(--card);color:var(--text);padding:20px 24px 38px;height:100%;transition:background .12s,color .12s;
      clip-path:polygon(0 0,100% 0,100% calc(100% - 28px),calc(100% - 28px) 100%,0 100%)}
    .p3-card>*{transition:transform .12s}
    .p3-title{font-family:var(--display);font-style:italic;text-transform:uppercase;font-size:1.7rem;line-height:1;margin:0 0 8px}
    .p3-meta{margin:0;opacity:.7;font-size:.9rem}
    .p3-rules{list-style:none;margin:16px 0 0;padding:0;display:grid;gap:9px}
    .p3-rules b{display:inline-block;width:1.5rem;font-size:1.2rem;color:var(--ok)}
    .p3-rules .cut b{color:var(--danger)}
    .p3-rules .cut s{text-decoration-color:var(--danger);text-decoration-thickness:.16em}
    .p3-heading{display:inline-block;margin:6px 0 22px;padding:5px 38px 5px 18px;background:var(--fg);color:var(--bg);
      font-family:var(--display);font-style:italic;text-transform:uppercase;font-size:1.4rem;clip-path:polygon(0 0,100% 0,94% 100%,0 100%)}
    .p3-cards{display:grid;gap:32px;grid-template-columns:repeat(auto-fill,minmax(250px,1fr))}
    .p3-cw.click{cursor:pointer}
    .p3-cw.click:hover,.p3-cw.click:focus-visible{transform:skewX(-5deg) translate(-3px,-3px)}
    .p3-cw.click:hover .p3-card,.p3-cw.click:focus-visible .p3-card{background:var(--accent);color:#fff}
    .p3-cw.click:hover .p3-card>*,.p3-cw.click:focus-visible .p3-card>*{transform:skewX(5deg)}
    .p3-cw.picked .p3-card{background:var(--yellow);color:#000}
    .p3-chip{display:inline-block;padding:2px 12px;margin-bottom:10px;font-size:.78rem;background:#000;color:var(--yellow);transform:skewX(-14deg)}
    .p3-chip.warn{background:var(--danger);color:#fff}
    .p3-bar{height:14px;margin-top:14px;background:rgba(127,127,127,.4);clip-path:polygon(0 0,100% 0,97% 100%,0 100%)}
    .p3-bar i{display:block;height:100%;width:var(--w);background:var(--yellow);clip-path:polygon(0 0,100% 0,93% 100%,0 100%)}
    @media (prefers-reduced-motion:reduce){.p3-new{animation:none}}
  `;
  document.head.append(el("style", { textContent: css }));

  const main = document.querySelector("main");
  const hero = el("section", { className: "p3-hero" });
  hero.innerHTML = `
    <div>
      <p class="p3-label">Clips ready</p>
      <div class="p3-numwrap"><span class="p3-big" id="p3-count">0</span><span class="p3-new" id="p3-new" hidden>New!!</span></div>
      <p class="p3-sub" id="p3-sub"></p>
      <div class="p3-actions">
        <button id="p3-upload"><span>Upload a video<i class="p3-key">A</i></span></button>
        <button id="p3-dash" class="secondary"><span>Dashboard<i class="p3-key">X</i></span></button>
      </div>
    </div>
    <aside class="p3-cw" id="p3-camp"></aside>`;
  const projects = el("section", {});
  projects.append(el("h2", { className: "p3-heading", textContent: "Projects" }), el("div", { className: "p3-cards", id: "p3-cards" }));
  main.prepend(hero, projects);

  // the old plain project list is replaced by the cards
  const oldList = document.getElementById("projects");
  if (oldList && oldList.closest("section")) oldList.closest("section").classList.add("hidden");

  document.getElementById("p3-upload").onclick = () => {
    document.getElementById("file").scrollIntoView({ behavior: "smooth", block: "center" });
    document.getElementById("file").click();
  };
  document.getElementById("p3-dash").onclick = () => { location.href = "/dashboard"; };

  // ---------- data ----------
  let cardSig = "", campSig = "";

  function hasNew(recent) {
    return recent.some((c) => c.status === "done" &&
      Date.now() - new Date(c.created_at.replace(" ", "T") + "Z").getTime() < 24 * 3600 * 1000);
  }

  function renderHero(d) {
    document.getElementById("p3-count").textContent = d.totals.clips_done;
    document.getElementById("p3-new").hidden = !hasNew(d.recent);
    const sub = document.getElementById("p3-sub");
    sub.replaceChildren();
    if (!d.totals.projects) { sub.append("Upload a video to get started."); return; }
    sub.append(`${d.totals.clips_done} finished clip${d.totals.clips_done === 1 ? "" : "s"} across ${d.totals.projects} project${d.totals.projects === 1 ? "" : "s"}. `);
    if (d.totals.clips_failed) {
      sub.append(el("span", { className: "p3-strike", textContent: `${d.totals.clips_failed} failed` }), " need another look.");
    }
  }

  function chipFor(p) {
    if (p.clips_failed) return ["Failed clips", true];
    if (p.transcript === "processing" || p.transcript === "queued") return ["Transcribing", false];
    if (!p.clips_total) return ["New", false];
    return ["Ready", false];
  }

  function renderCards(d) {
    const sig = JSON.stringify([d.projects.map((p) => [p.id, p.clips_done, p.clips_total, p.clips_failed, p.transcript]),
      currentProject && currentProject.id]);
    if (sig === cardSig) return;
    cardSig = sig;
    const box = document.getElementById("p3-cards");
    box.replaceChildren();
    if (!d.projects.length) { box.append(el("p", { className: "muted", textContent: "No projects yet." })); return; }
    d.projects.forEach((p) => {
      const [chipText, warn] = chipFor(p);
      const pct = p.clips_total ? Math.round((100 * p.clips_done) / p.clips_total) : 0;
      const card = el("div", { className: "p3-card" });
      card.append(
        el("span", { className: "p3-chip" + (warn ? " warn" : ""), textContent: chipText }),
        el("div", { className: "p3-title", textContent: p.name }),
        el("p", { className: "p3-meta", textContent: `${fmtTime(p.duration)}, ${p.clips_total ? `${p.clips_done} of ${p.clips_total} clips done` : "no clips yet"}` }),
      );
      const bar = el("div", { className: "p3-bar" }, el("i"));
      bar.firstChild.style.setProperty("--w", pct + "%");
      card.append(bar);
      const wrap = el("div", { className: "p3-cw click" + (currentProject && currentProject.id === p.id ? " picked" : ""), tabIndex: 0 }, card);
      wrap.setAttribute("role", "button");
      const open = () => {
        selectProject(p);
        cardSig = "";
        setTimeout(() => document.getElementById("project-panel").scrollIntoView({ behavior: "smooth" }), 120);
      };
      wrap.onclick = open;
      wrap.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } };
      box.append(wrap);
    });
  }

  function ruleLines(r) {
    const lines = [];
    if (r.min_seconds || r.max_seconds) lines.push([true, `Length ${r.min_seconds || "?"} to ${r.max_seconds || "?"} seconds`]);
    else if (r.preferred_seconds) lines.push([true, `Length about ${r.preferred_seconds} seconds`]);
    if (r.platforms.some((x) => /tiktok|reels|shorts|vertical|9:16/i.test(x))) lines.push([true, "Vertical 9:16"]);
    r.required_spoken_phrases.forEach((x) => lines.push([true, `Says \u201C${x}\u201D`]));
    if (r.required_hashtags.length || r.required_caption_text.length) lines.push([false, "Hashtags and post text"]);
    r.required_visuals.forEach((x) => lines.push([false, x]));
    r.banned_content.forEach((x) => lines.push([false, `No ${x}`]));
    r.other_rules.forEach((x) => lines.push([false, x]));
    return lines;
  }

  function renderCampaign(name, rules) {
    const box = document.getElementById("p3-camp");
    const sig = JSON.stringify([name, rules]);
    if (sig === campSig) return;
    campSig = sig;
    box.replaceChildren();
    const card = el("div", { className: "p3-card" });
    if (!rules) {
      card.append(el("div", { className: "p3-title", textContent: "No campaign yet" }),
        el("p", { className: "p3-meta", textContent: "Upload a campaign brief and the app drafts the rules and checks every clip against them." }),
        el("p", {}, el("a", { href: "/campaigns", textContent: "Create a campaign" })));
      return box.append(card);
    }
    card.append(el("div", { className: "p3-title", textContent: name }),
      el("p", { className: "p3-meta", textContent: rules.summary || "Rules from your campaign brief" }));
    const ul = el("ul", { className: "p3-rules" });
    const lines = ruleLines(rules);
    lines.slice(0, 7).forEach(([auto, text]) => {
      const li = el("li", { className: auto ? "" : "cut" });
      li.append(el("b", { textContent: auto ? "\u2713" : "\u2717" }));
      if (auto) li.append(text); else li.append(el("s", { textContent: text }), " check by hand");
      ul.append(li);
    });
    if (lines.length > 7) ul.append(el("li", { className: "p3-meta", textContent: `+ ${lines.length - 7} more on the Campaigns page` }));
    if (!lines.length) ul.append(el("li", { className: "p3-meta", textContent: "No rules yet. Review this campaign on the Campaigns page." }));
    card.append(ul);
    box.append(card);
  }

  async function loadCampaign() {
    try {
      if (currentProject) {
        const own = await api("GET", `/api/projects/${currentProject.id}/campaign`);
        if (own.campaign_id) return renderCampaign(own.name, own.rules);
      }
      const list = (await api("GET", "/api/campaigns")).campaigns.filter((c) => c.status === "ready");
      if (!list.length) return renderCampaign(null, null);
      const c = await api("GET", `/api/campaigns/${list[0].id}`);
      renderCampaign(c.name, c.rules);
    } catch (_) {}
  }

  async function refresh() {
    try {
      const d = await api("GET", "/api/dashboard");
      renderHero(d);
      renderCards(d);
    } catch (_) {}
    loadCampaign();
  }

  refresh();
  setInterval(refresh, 4000);
})();n