// Persona-style page transitions. Loaded on every page (main page: automatically, as static/ext_*.js).
(() => {
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;

  const wipe = document.createElement("div");
  wipe.className = "wipe";
  wipe.setAttribute("aria-hidden", "true");
  wipe.innerHTML = "<i></i><i></i><i></i>";
  document.body.append(wipe);

  // highlight the page we are on
  document.querySelectorAll("header .nav a").forEach((a) => {
    if (a.pathname === location.pathname) a.classList.add("current");
  });

  // arriving from another page: the slabs start over the screen, then sweep away
  if (!reduce && sessionStorage.getItem("persona-wipe")) {
    sessionStorage.removeItem("persona-wipe");
    wipe.classList.add("out");
    document.body.classList.add("persona-in");
    setTimeout(() => { wipe.classList.remove("out"); document.body.classList.remove("persona-in"); }, 1000);
  }

  // leaving: slabs slash across, then the browser moves to the next page
  document.addEventListener("click", (e) => {
    const a = e.target.closest("a[href]");
    if (!a || reduce || e.defaultPrevented || e.button !== 0) return;
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    if (a.target || a.hasAttribute("download") || a.origin !== location.origin) return;
    if (a.pathname.startsWith("/api/")) return;
    if (a.pathname === location.pathname && a.search === location.search) return;   // same page
    e.preventDefault();
    sessionStorage.setItem("persona-wipe", "1");
    wipe.classList.remove("out", "in");
    void wipe.offsetWidth;
    wipe.classList.add("in");
    setTimeout(() => { location.href = a.href; }, 440);
  });

  // coming back with the browser's Back button: don't leave the slabs on screen
  window.addEventListener("pageshow", (e) => {
    if (e.persisted) wipe.classList.remove("in", "out");
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
})();