"use strict";
(() => {
  let updates = [], read = new Set(), userId = null, generation = 0, timer, loading = false;
  const storageKey = () => `transcriptske.read-updates.${userId}`;
  function saveRead() {
    // Store opaque update identifiers only; names, messages and email stay in memory.
    try { localStorage.setItem(storageKey(), JSON.stringify([...read].slice(-500))); } catch { /* Optional browser storage. */ }
  }
  function close(focus = false) {
    $("notification-panel").hidden = true;
    $("notification-toggle").setAttribute("aria-expanded", "false");
    if (focus) $("notification-toggle").focus();
  }
  function render() {
    const unread = updates.filter(update => !read.has(update.key)).length;
    $("notification-count").hidden = !unread;
    $("notification-count").textContent = unread > 99 ? "99+" : String(unread);
    $("notification-toggle").setAttribute("aria-label", unread ? `Notifications, ${unread} unread` : "Notifications");
    $("notification-read").disabled = !unread;
    $("notification-list").replaceChildren();
    for (const update of updates) {
      const button = node("button", undefined, `notification-item${read.has(update.key) ? " is-read" : ""}`);
      button.type = "button";
      button.append(node("strong", update.title), node("span", update.description));
      const date = new Date(update.date), time = node("time", Number.isNaN(date.valueOf()) ? "" : date.toLocaleString());
      if (!Number.isNaN(date.valueOf())) time.dateTime = date.toISOString();
      button.append(time);
      button.addEventListener("click", () => run(async () => {
        const originalUser = userId;
        close();
        if (update.kind === "record") await openEnrollmentProcess(update.id);
        else await window.ordersWorkspace.open(update.id);
        if (userId !== originalUser || !currentUser) return;
        read.add(update.key); saveRead(); render();
      }));
      $("notification-list").append(button);
    }
    if (!updates.length) $("notification-list").append(node("p", "You're all caught up. Enrollment and submitted request updates will appear here.", "notification-empty"));
  }
  async function refresh() {
    if (!currentUser || loading) return;
    const sequence = generation, owner = userId;
    loading = true; $("notification-refresh").disabled = true;
    $("notification-status").textContent = "Checking your updates…";
    try {
      // Current owner-scoped summaries, bounded to the most recent 100 of each.
      const [records, orders] = await Promise.all([
        api("/me/academic-record-links?offset=0&limit=100"), api("/orders?offset=0&limit=100")
      ]);
      if (sequence !== generation || owner !== userId) return;
      const titles = {pending: "Enrollment under review", matched: "Enrollment confirmed", needs_information: "Enrollment needs more information", rejected: "Enrollment could not be confirmed"};
      const descriptions = {pending: "Open your enrollment for the next step.", matched: "You can continue with your document request.", needs_information: "Open your enrollment to review the requested changes.", rejected: "Review the institution's feedback and update your details."};
      updates = records.map(record => ({kind: "record", id: record.id, key: `r:${record.id}:${record.version}`, date: record.updated_at,
        title: record.checkout_required ? "Enrollment saved — continue to checkout" : titles[record.status] || "Enrollment updated", description: `${publicInstitutions.find(item => item.id === record.institution_id)?.name || "Your institution"} · ${descriptions[record.status] || "Open your enrollment for details."}`}));
      for (const order of orders.filter(item => item.submitted_at)) {
        updates.push({kind: "order", id: order.id, key: `o:${order.id}:${order.version}:${order.payment_status}:${order.unanswered_questions}`, date: order.updated_at,
          title: order.unanswered_questions ? "Your institution needs a reply" : `Request ${order.status.replaceAll("_", " ")}`,
          description: `${order.reference} · ${order.unanswered_questions ? "Open your request to answer the registrar." : `Payment: ${order.payment_status.replaceAll("_", " ")}. Open for progress and next steps.`}`});
      }
      updates.sort((a, b) => Date.parse(b.date) - Date.parse(a.date));
      render(); $("notification-status").textContent = "Showing the latest status for your most recent records and requests.";
    } catch {
      if (sequence === generation && currentUser) $("notification-status").textContent = "Unable to check updates. Please try Refresh.";
    } finally {
      if (sequence === generation) { loading = false; $("notification-refresh").disabled = false; }
    }
  }
  function reset() {
    generation++; clearInterval(timer); timer = null; loading = false;
    userId = null; updates = []; read.clear(); close();
    $("header-notifications").hidden = true;
    $("notification-list").replaceChildren(); $("notification-status").textContent = "";
    $("notification-count").hidden = true; $("notification-count").textContent = "";
    $("notification-toggle").setAttribute("aria-label", "Notifications");
  }
  function load() {
    reset(); userId = currentUser.id;
    try {
      const saved = JSON.parse(localStorage.getItem(storageKey()) || "[]");
      read = new Set(Array.isArray(saved) ? saved.filter(item => typeof item === "string" && /^[ro]:[0-9:]+(?:[a-z_]+:[0-9]+)?$/.test(item)).slice(-500) : []);
    } catch { read = new Set(); }
    $("header-notifications").hidden = false; refresh();
    timer = setInterval(() => { if (!document.hidden) refresh(); }, 60000);
  }
  $("notification-toggle").addEventListener("click", () => {
    const open = $("notification-panel").hidden;
    UI_accountMenu(false); close();
    if (open) { $("notification-panel").hidden = false; $("notification-toggle").setAttribute("aria-expanded", "true"); refresh(); }
  });
  $("notification-toggle").addEventListener("keydown", event => {
    if (event.key !== "ArrowDown") return;
    event.preventDefault(); UI_accountMenu(false);
    $("notification-panel").hidden = false; $("notification-toggle").setAttribute("aria-expanded", "true");
    $("notification-refresh").focus(); refresh();
  });
  $("notification-close").addEventListener("click", () => close(true));
  $("notification-refresh").addEventListener("click", refresh);
  $("notification-read").addEventListener("click", () => { updates.forEach(update => read.add(update.key)); saveRead(); render(); });
  document.addEventListener("click", event => { if (!$("header-notifications").contains(event.target)) close(); });
  document.addEventListener("keydown", event => { if (event.key === "Escape" && !$("notification-panel").hidden) { event.preventDefault(); close(true); } });
  $("header-notifications").addEventListener("focusout", event => { if (event.relatedTarget && !$("header-notifications").contains(event.relatedTarget)) close(); });
  document.addEventListener("visibilitychange", () => { if (!document.hidden && currentUser) refresh(); });
  window.addEventListener("storage", event => {
    if (currentUser && event.key === storageKey()) {
      try { const saved = JSON.parse(event.newValue || "[]"); if (Array.isArray(saved)) read = new Set(saved.filter(item => typeof item === "string").slice(-500)); render(); } catch { /* Ignore invalid browser storage. */ }
    }
  });
  window.notificationsWorkspace = {load, reset, close};
})();
