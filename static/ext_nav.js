// Adds the Dashboard link to the main page's header (auto-loaded: static/ext_*.js).
(function addDashboardLink() {
  const nav = document.querySelector("header .nav");
  if (!nav || nav.querySelector('a[href="/dashboard"]')) return;
  const link = document.createElement("a");
  link.href = "/dashboard";
  link.textContent = "Dashboard";
  nav.insertBefore(link, nav.children[1] || null);
})();