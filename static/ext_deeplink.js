// Opens a project from a link like /#project=3 (used by the Dashboard). Uses helpers from app.js.
(async function openProjectFromLink() {
  const match = location.hash.match(/project=(\d+)/);
  if (!match) return;
  try {
    const projects = await api("GET", "/api/projects");
    const project = projects.find((p) => p.id === Number(match[1]));
    if (project) selectProject(project);
  } catch (_) {}
})();