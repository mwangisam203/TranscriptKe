"use strict";
(() => {
  const key = "transcriptske.color-theme";
  const system = window.matchMedia("(prefers-color-scheme: dark)");
  const valid = value => ["light", "dark", "system"].includes(value);
  let preference = "system", beamTimer;
  try { const saved = localStorage.getItem(key); if (valid(saved)) preference = saved; } catch { /* Storage may be disabled. */ }
  function apply() {
    const theme = preference === "system" ? (system.matches ? "dark" : "light") : preference;
    document.documentElement.dataset.theme = theme;
    const toggle = document.getElementById("theme-preference");
    if (toggle) {
      toggle.setAttribute("aria-pressed", String(theme === "dark"));
      toggle.setAttribute("aria-label", "Dark mode");
      toggle.title = theme === "dark" ? "Switch to light mode" : "Switch to dark mode";
      document.getElementById("theme-label").textContent = theme === "dark" ? "Dark" : "Light";
    }
  }
  apply(); system.addEventListener("change", apply);
  window.addEventListener("storage", event => {
    if (event.key !== key && event.key !== null) return;
    preference = valid(event.newValue) ? event.newValue : "system"; apply();
  });
  document.addEventListener("DOMContentLoaded", () => {
    apply();
    document.getElementById("theme-preference")?.addEventListener("click", () => {
      preference = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      try { localStorage.setItem(key, preference); } catch { /* Still apply for this page. */ }
      apply();
      clearTimeout(beamTimer);
      document.documentElement.classList.remove("theme-beam");
      if (preference === "dark") {
        // Restart the beam only for a deliberate switch, never on page load.
        void document.documentElement.offsetWidth;
        document.documentElement.classList.add("theme-beam");
        beamTimer = setTimeout(() => document.documentElement.classList.remove("theme-beam"), 900);
      }
    });
  });
})();
