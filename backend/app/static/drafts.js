"use strict";
// Encrypted, account-owned recovery copies; no passwords, files, tokens, or signatures.
window.draftsWorkspace = (() => {
  const cache = new Map(), timers = new Map(), pending = new Map(), revisions = new Map();
  let ready = false, generation = 0, writing = Promise.resolve();
  const states = {"checkout-profile": "checkout-profile-draft-state", enrollment: "enrollment-draft-state", "order-start": "order-start-draft-state"};
  const status = (key, text) => { const element = $(states[key] || "order-draft-state"); if (element) element.textContent = text; };
  function fields(form) {
    const data = {};
    for (const input of form.elements) {
      const key = input.name || input.id;
      if (!key || ["file", "password", "submit", "button"].includes(input.type)) continue;
      data[key] = input.type === "checkbox" ? input.checked : input.value;
    }
    return data;
  }
  function fill(form, data) {
    for (const input of form.elements) {
      const key = input.name || input.id;
      if (!(key in data) || ["file", "password"].includes(input.type)) continue;
      if (input.type === "checkbox") input.checked = Boolean(data[key]); else input.value = data[key];
    }
  }
  async function read(key) { const row = await api(`/me/workspace-drafts/${key}`); cache.set(key, row); return row.data; }
  function queue(key, data) {
    if (!ready || !currentUser || cache.get(key)?.conflict) return;
    revisions.set(key, (revisions.get(key) || 0) + 1);
    pending.set(key, data); clearTimeout(timers.get(key)); status(key, "Saving draft…");
    timers.set(key, setTimeout(() => save(key), 650));
  }
  function save(key) {
    clearTimeout(timers.get(key)); timers.delete(key);
    if (!pending.has(key)) return writing;
    const data = pending.get(key); pending.delete(key); const localGeneration = generation, revision = revisions.get(key);
    writing = writing.then(async () => {
      if (localGeneration !== generation || !currentUser || cache.get(key)?.conflict) return;
      try {
        const row = await api(`/me/workspace-drafts/${key}`, "PUT", {expected_version: cache.get(key)?.version || 0, data});
        if (localGeneration !== generation) return;
        cache.set(key, row);
        if (key === "enrollment") $("resume-enrollment-draft").hidden = !Object.keys(data).length;
        status(key, revisions.get(key) !== revision ? "Saving draft…" : "Draft saved automatically. Re-select files after refresh.");
      } catch (error) {
        if (localGeneration !== generation) return;
        cache.set(key, {...cache.get(key), conflict: true}); status(key, `Draft was not saved: ${error.message}`);
      }
    });
    return writing;
  }
  async function flush() { for (const key of [...pending.keys()]) save(key); await writing; }
  async function clear(key) {
    clearTimeout(timers.get(key)); timers.delete(key); pending.delete(key); await writing;
    if (cache.get(key)?.conflict) throw new Error("Reload the changed draft before continuing.");
    const row = await api(`/me/workspace-drafts/${key}`, "PUT", {expected_version: cache.get(key)?.version || 0, data: {}});
    cache.set(key, row); status(key, "");
    if (key === "enrollment") $("resume-enrollment-draft").hidden = true;
  }
  async function load() {
    ready = false; const localGeneration = generation;
    const enrollment = await read("enrollment"), start = await read("order-start");
    if (localGeneration !== generation || !currentUser) return;
    $("resume-enrollment-draft").hidden = !Object.keys(enrollment).length;
    if (Object.keys(enrollment).length) {
      const form = $("record-form"); fill(form, enrollment);
      if (form.elements.institution_id.value) await loadStudentCatalog();
      fill(form, enrollment); updateLookupMethod(); updateIdentityImages(); updateRequirements();
      status("enrollment", "Unfinished enrollment details restored. Re-select ID images before submitting.");
    }
    if (Object.keys(start).length) {
      fill($("new-order-form"), start); O_recordChoices(); fill($("new-order-form"), start); O_destination();
      status("order-start", "Your unfinished document request is restored below.");
    }
    const profileDraft = await read("checkout-profile");
    if (localGeneration !== generation || !currentUser) return;
    if (Object.keys(profileDraft).length) { fill($("checkout-profile-form"), profileDraft); $("checkout-personal-details").open = true; status("checkout-profile", "Unfinished personal details restored."); }
    ready = true;
    try {
      const position = JSON.parse(sessionStorage.getItem(`workspace-position-${currentUser.id}`) || "null");
      if (position?.order) { await O_open(position.order); showView("orders-view"); }
    } catch (error) { status("order-start", error.message); }
  }
  async function restoreOrder(order) {
    const data = await read(`order-${order.id}`);
    if (Object.keys(data).length && data.expected_version === order.version) {
      fillForm($("order-editor"), data); $("order-recipients").replaceChildren(); $("order-items").replaceChildren();
      (data.recipients || []).forEach(O_addRecipient); (data.items || []).forEach(O_addItem); O_changed();
      status(`order-${order.id}`, "Unfinished changes restored. Review them before requesting a quote.");
    } else status(`order-${order.id}`, Object.keys(data).length ? "The order changed. Showing its latest saved details." : "Changes are saved automatically as a draft.");
  }
  for (const [id, key] of [["record-form", "enrollment"], ["new-order-form", "order-start"], ["checkout-profile-form", "checkout-profile"]]) {
    const capture = () => { if (key === "enrollment" && revisingLink) return; queue(key, fields($(id))); };
    $(id).addEventListener("input", capture); $(id).addEventListener("change", capture);
  }
  const captureOrder = () => { if (O_current?.status === "draft") queue(`order-${O_current.id}`, O_draftBody()); };
  $("order-editor").addEventListener("input", captureOrder); $("order-editor").addEventListener("change", captureOrder);
  $("order-editor").addEventListener("click", event => { if (event.target.type === "button") setTimeout(captureOrder, 0); });
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") flush(); });
  window.addEventListener("pagehide", () => {
    if (!currentUser || !accessToken) return;
    for (const [key, data] of pending) {
      if (cache.get(key)?.conflict) continue;
      fetch(`/api/v1/me/workspace-drafts/${key}`, {method: "PUT", keepalive: true,
        headers: {"Content-Type": "application/json", Authorization: `Bearer ${accessToken}`},
        body: JSON.stringify({expected_version: cache.get(key)?.version || 0, data})}).catch(() => {});
    }
  });
  async function resumeEnrollment() {
    await flush(); const data = await read("enrollment");
    if (!Object.keys(data).length) return;
    ready = false; clearRevision(); showView("student-view");
    const form = $("record-form"); fill(form, data);
    try {
      if (form.elements.institution_id.value) await loadStudentCatalog();
      fill(form, data); updateLookupMethod(); updateIdentityImages(); updateRequirements();
      status("enrollment", "Saved enrollment draft restored. Continue from where you stopped.");
      form.scrollIntoView({block: "start"}); $("record-form-title").tabIndex = -1; $("record-form-title").focus({preventScroll: true});
    } finally { ready = true; }
  }
  $("resume-enrollment-draft").addEventListener("click", () => run(resumeEnrollment));
  return {load, restoreOrder, clear, flush, resumeEnrollment, forget(key) { clearTimeout(timers.get(key)); timers.delete(key); pending.delete(key); cache.delete(key); }, reset() {
    $("resume-enrollment-draft").hidden = true;
    generation++; ready = false; timers.forEach(clearTimeout); timers.clear(); pending.clear(); cache.clear(); revisions.clear();
    for (const id of ["enrollment-draft-state", "order-start-draft-state", "order-draft-state", "checkout-profile-draft-state"]) $(id).textContent = "";
  }};
})();
