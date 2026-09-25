"use strict";
const P_states = {staff: {generation: 0}, admin: {generation: 0}};
function P_reset(role) {
  for (const key of role ? [role] : ["staff", "admin"]) {
    P_states[key].generation++;
    const panel = $(`pilot-${key}`); panel.hidden = true; panel.replaceChildren();
  }
}
function P_evidence(form, requirements, values = {}) {
  const section = node("details"); section.open = true; section.append(node("summary", "Private acceptance evidence"));
  section.append(node("p", "Record who checked each requirement, when, the result and an internal evidence reference. Do not paste credentials or student records.", "muted"));
  for (const [key, label] of Object.entries(requirements)) {
    const wrap = node("label", label), input = document.createElement("textarea"); input.name = key; input.maxLength = 2000; input.value = values[key] || ""; wrap.append(input); section.append(wrap);
  }
  form.append(section);
}
function P_evidenceValue(form, requirements) {return Object.fromEntries(Object.keys(requirements).map((key) => [key, form.elements[key].value]));}
function P_snapshot(container, snapshot) {
  container.append(node("p", `${snapshot.mode.toUpperCase()} · Observed ${OP_date(snapshot.captured_at)}`));
  container.append(node("p", `Submission window: ${OP_date(snapshot.window_start)} (inclusive) to ${OP_date(snapshot.window_end)} (exclusive)`));
  for (const [key, value] of Object.entries(snapshot.counts)) container.append(node("p", `${key.replaceAll("_", " ")}: ${value}`));
  container.append(node("p", snapshot.measurement, "muted"));
  const operations = node("details"); operations.append(node("summary", "Institution operations at capture"));
  for (const [key, value] of Object.entries(snapshot.current_operations)) operations.append(node("p", `${OP_labels[key] || key.replaceAll("_", " ")}: ${value}`));
  container.append(operations);
  for (const blocker of snapshot.expansion_blockers) container.append(node("p", `Expansion requirement: ${blocker}`));
  container.append(node("p", snapshot.limitation, "muted"));
}
async function P_load(role, id, context = null) {
  const state = P_states[role], generation = ++state.generation, session = sessionGeneration;
  const panel = $(`pilot-${role}`); panel.replaceChildren(); panel.hidden = true;
  if (!id || (role === "staff" && !context?.manager)) return;
  const valid = () => generation === state.generation && session === sessionGeneration && (role === "staff" ? staffContext === context : Number($("admin-institution").value) === Number(id));
  const guard = () => {if (!valid()) throw new Error("Reload this institution before saving.");};
  const base = `/${role}/institutions/${id}`;
  const [onboarding, evaluations] = await Promise.all([api(base + "/onboarding"), api(base + "/pilot/evaluations")]);
  if (!valid()) return;
  panel.hidden = false;
  panel.append(node("h2", "Onboarding and pilot evaluation"), action("Refresh pilot review", () => P_load(role, id, context)), node("p", `Onboarding: ${onboarding.status.replaceAll("_", " ")}`));
  if (onboarding.legacy_approval) panel.append(node("p", "Existing approval is preserved. It does not prove pilot acceptance.", "muted"));
  if (onboarding.review_reason) panel.append(node("p", `Administrator feedback: ${onboarding.review_reason}`));
  const reload = async () => {if (valid()) await P_load(role, id, context);};
  if (role === "staff" && ["not_started", "draft", "changes_requested"].includes(onboarding.status)) {
    F_form(panel, "Prepare institution onboarding", (form) => P_evidence(form, onboarding.requirements, onboarding.evidence), "Save onboarding evidence", async (form) => {
      guard(); await api(base + "/onboarding", "PUT", {expected_version: onboarding.version, evidence: P_evidenceValue(form, onboarding.requirements)}); await reload(); notice("Onboarding evidence saved.");
    });
    if (onboarding.version) panel.append(action("Submit onboarding for review", async () => {guard(); await api(base + "/onboarding/submit", "POST", {expected_version: onboarding.version}); await reload(); notice("Onboarding submitted for independent review.");}));
  } else {
    const details = node("details"); details.append(node("summary", "Submitted onboarding evidence"));
    for (const [key, label] of Object.entries(onboarding.requirements)) details.append(node("p", `${label}: ${onboarding.evidence[key] || "Not supplied"}`, "history"));
    panel.append(details);
  }
  for (const blocker of onboarding.blockers) panel.append(node("p", blocker, "muted"));
  if (role === "admin" && onboarding.status === "submitted") F_form(panel, "Review onboarding", (form) => {
    O_select(form, "Decision", "decision", [["changes_requested", "Request changes"], ["approved", "Accept onboarding evidence"]]);
    const reason = O_input(form, "Review reason (at least 20 characters)", "reason"); reason.required = true; reason.minLength = 20; reason.maxLength = 4000;
  }, "Save onboarding decision", async (form) => {guard(); await api(base + "/onboarding/review", "POST", {expected_version: onboarding.version, ...formObject(form)}); await reload(); notice("Onboarding decision saved. Institution approval is a separate action above.");});
  if (onboarding.history.length) {
    const history = node("details"); history.append(node("summary", "Onboarding audit history (latest 50)"));
    for (const entry of onboarding.history) history.append(node("p", `${OP_date(entry.created_at)} · ${entry.action.replaceAll("_", " ")} · actor ${entry.actor_id}`, "history"));
    panel.append(history);
  }
  if (role === "staff") F_form(panel, "Evaluate a pilot cohort", (form) => {
    O_select(form, "Evidence mode", "mode", [["demo", "Demo / test"], ["live", "Live"]]);
    const end = new Date(), start = new Date(end.getTime() - 30 * 86400000);
    const local = (date) => new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
    O_input(form, "Submitted from (local time, inclusive)", "window_start", local(start), "datetime-local").required = true;
    O_input(form, "Submitted before (local time, exclusive)", "window_end", local(end), "datetime-local").required = true;
    form.append(node("p", "The dates select submitted orders. Outcomes are measured now. Complete fresh acceptance evidence for this evaluation.", "muted"));
    P_evidence(form, onboarding.requirements);
    const findings = O_input(form, "Pilot findings and unresolved issues (at least 20 characters)", "findings"); findings.required = true; findings.minLength = 20; findings.maxLength = 4000;
    const preview = node("div"); preview.className = "pilot-preview";
    form.append(action("Preview pilot evidence", async () => {
      guard(); const snapshot = await api(base + "/pilot/preview", "POST", P_window(form)); if (!valid()) return;
      preview.replaceChildren(); P_snapshot(preview, snapshot);
    }), preview);
  }, "Submit pilot evaluation", async (form) => {
    guard(); await api(base + "/pilot/evaluations", "POST", {...P_window(form), evidence: P_evidenceValue(form, onboarding.requirements), findings: form.elements.findings.value}); await reload(); notice("Pilot evaluation saved with an immutable evidence snapshot.");
  });
  const history = node("div"); panel.append(node("h3", "Pilot evaluations"), history);
  function render(items) {
    for (const item of items) {
      const card = node("details"); card.append(node("summary", `${item.mode.toUpperCase()} · ${item.decision.replaceAll("_", " ")} · ${OP_date(item.created_at)}`), node("p", item.findings, "history"));
      card.append(node("p", `Submitted by account ${item.created_by}${item.reviewed_by ? ` · Reviewed by account ${item.reviewed_by} at ${OP_date(item.reviewed_at)}` : ""}`, "muted"));
      P_snapshot(card, item.snapshot);
      for (const [key, label] of Object.entries(onboarding.requirements)) card.append(node("p", `${label}: ${item.evidence[key]}`, "history"));
      if (item.review_reason) card.append(node("p", `Review: ${item.review_reason}`, "history"));
      if (item.review_snapshot) {const review = node("details"); review.append(node("summary", "Evidence rechecked at decision")); P_snapshot(review, item.review_snapshot); card.append(review);}
      if (role === "admin" && item.decision === "pending") F_form(card, "Independent pilot decision", (form) => {
        O_select(form, "Pilot decision", "decision", [["continue_pilot", "Continue pilot"], ["rework", "Rework workflow"], ["expand", "Approve expansion planning"]]);
        const reason = O_input(form, "Decision evidence and next steps", "reason"); reason.required = true; reason.minLength = 20; reason.maxLength = 4000;
      }, "Save pilot decision", async (form) => {guard(); await api(base + `/pilot/evaluations/${item.id}/review`, "POST", {expected_version: item.version, ...formObject(form)}); await reload(); notice("Pilot decision recorded. Deployment and merchant configuration remain separate.");});
      history.append(card);
    }
  }
  render(evaluations.items);
  if (!evaluations.total) history.append(node("p", "No pilot evaluations submitted yet."));
  let offset = evaluations.items.length, loading = false;
  const more = action("Load more pilot evaluations", async () => {
    guard(); if (loading) return; loading = true;
    try {const page = await api(base + `/pilot/evaluations?offset=${offset}`); if (!valid()) return; render(page.items); offset += page.items.length; more.hidden = offset >= page.total;} finally {loading = false;}
  }); more.hidden = offset >= evaluations.total; panel.append(more);
}
function P_window(form) {return {mode: form.elements.mode.value, window_start: new Date(form.elements.window_start.value).toISOString(), window_end: new Date(form.elements.window_end.value).toISOString()};}
bindForm("create-institution-form", async (form) => {
  const session = sessionGeneration;
  const item = await api("/admin/institutions", "POST", formObject(form));
  if (session !== sessionGeneration) return;
  adminInstitutions.push(item); options($("admin-institution"), adminInstitutions, (entry) => entry.name); $("admin-institution").value = item.id; form.reset(); showApproval(); notice("Institution created unapproved. Invite its manager, configure services and complete onboarding.");
});
window.pilotWorkspace = {loadStaff: (context) => P_load("staff", context.id, context), loadAdmin: (id) => P_load("admin", id), reset: P_reset};
