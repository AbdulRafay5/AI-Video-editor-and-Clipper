// Persona-style page transitions and panel framing. Loaded on every page (main page: automatically).
(() => {
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  // wrap each panel so it can carry the hard offset shadow and the yellow tab
  document.querySelectorAll("main > .card").forEach((card) => {
    const wrap = document.createElement("div");
    wrap.className = "cw";
    card.replaceWith(wrap);
    wrap.append(card);
  });

  // highlight the page we are on
  document.querySelectorAll("header .nav a").forEach((a) => {
    if (a.pathname === location.pathname) a.classList.add("current");
  });

  // opening a project zooms its panel in
  const panel = document.getElementById("project-panel");
  if (panel && !reduce) {
    let wasHidden = panel.classList.contains("hidden");
    new MutationObserver(() => {
      const hidden = panel.classList.contains("hidden");
      if (wasHidden && !hidden) {
        panel.classList.remove("pp-enter");
        void panel.offsetWidth;
        panel.classList.add("pp-enter");
      }
      wasHidden = hidden;
    }).observe(panel, { attributes: true, attributeFilter: ["class"] });
  }

  if (reduce) return;

  // ---- slabs slash across before navigating, then sweep away on the next page ----
  const wipe = document.createElement("div");
  wipe.className = "wipe";
  wipe.setAttribute("aria-hidden", "true");
  wipe.innerHTML = "<i></i><i></i><i></i>";
  document.body.append(wipe);

  if (sessionStorage.getItem("persona-wipe")) {
    sessionStorage.removeItem("persona-wipe");
    wipe.classList.add("out");
    document.body.classList.add("persona-in");
    document.documentElement.classList.remove("persona-arriving");
    setTimeout(() => { wipe.classList.remove("out"); document.body.classList.remove("persona-in"); }, 800);
  }

  document.addEventListener("click", (e) => {
    const a = e.target.closest("a[href]");
    if (!a || e.defaultPrevented || e.button !== 0) return;
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    if (a.target || a.hasAttribute("download") || a.origin !== location.origin) return;
    if (a.pathname.startsWith("/api/")) return;
    if (a.pathname === location.pathname && a.search === location.search) return;
    e.preventDefault();
    sessionStorage.setItem("persona-wipe", "1");
    wipe.classList.remove("out", "in");
    void wipe.offsetWidth;
    wipe.classList.add("in");
    setTimeout(() => { location.href = a.href; }, 720);
  });

  window.addEventListener("pageshow", (e) => {
    if (e.persisted) wipe.classList.remove("in", "out");
  });
})();