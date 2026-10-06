"use strict";
(() => {
  const key = "transcriptske.color-theme";
  const system = window.matchMedia("(prefers-color-scheme: dark)");
  const valid = (value) => ["light", "dark", "system"].includes(value);
  let preference = "system";
  try { const saved = localStorage.getItem(key); if (valid(saved)) preference = saved; } catch { /* Storage may be disabled. */ }
  function apply() {
    document.documentElement.dataset.theme = preference === "system" ? (system.matches ? "dark" : "light") : preference;
    const picker = document.getElementById("theme-preference");
    if (picker) picker.value = preference;
  }
  apply();
  system.addEventListener("change", apply);
  window.addEventListener("storage", (event) => {
    if (event.key !== key && event.key !== null) return;
    preference = valid(event.newValue) ? event.newValue : "system";
    apply();
  });
  document.addEventListener("DOMContentLoaded", () => {
    apply();
    document.getElementById("theme-preference")?.addEventListener("change", (event) => {
      preference = event.target.value;
      try { localStorage.setItem(key, preference); } catch { /* Still apply for this page. */ }
      apply();
    });
  });
})();
