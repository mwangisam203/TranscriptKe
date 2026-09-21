"use strict";
async function F_student(order) {
  const progress = await api(`/orders/${order.id}/fulfillment`);
  if (O_current !== order) return;
  const container = $("student-fulfillment"); container.replaceChildren(node("h3", "Document progress"));
  for (const item of progress.items) {
    const name = order.submitted_snapshot?.items.find((i) => i.key === item.key)?.name || item.key;
    container.append(node("p", `${name}: ${item.status.replaceAll("_", " ")}`));
  }
  for (const hold of progress.holds) container.append(node("p", `${hold.resolved_at ? "Resolved hold" : "On hold"}: ${hold.student_message}${hold.resolution ? ` — ${hold.resolution}` : ""}`));
}
function F_form(container, title, controls, submitLabel, handler) {
  const form = document.createElement("form"); form.append(node("h4", title)); controls(form);
  const button = node("button", submitLabel); form.append(button);
  form.addEventListener("submit", (e) => {e.preventDefault(); if (button.disabled) return; button.disabled = true; run(() => handler(form)).finally(() => {button.disabled = false;});});
  container.append(form);
}
function F_notes(form) {
  for (const [name, label] of [["student_message", "Message visible to the student"], ["internal_note", "Internal registrar note (staff only)"]]) {
    const input = O_input(form, label, name); input.required = true; input.maxLength = 2000;
  }
}
async function F_staff(context, order) {
  const base = `/staff/institutions/${context.id}/orders/${order.id}/fulfillment`;
  const progress = await api(base);
  const registrars = []; let page;
  do { page = await api(`/staff/institutions/${context.id}/registrars?offset=${registrars.length}&limit=100`); registrars.push(...page); } while (page.length === 100);
  if (O_staff !== order || staffContext !== context) return;
  const container = $("registrar-workspace"); container.replaceChildren(node("h3", "Registrar review"));
  container.append(node("p", progress.assigned_to ? `Assigned to ${registrars.find((r) => r.id === progress.assigned_to)?.name || "a former staff member (reassignment required)"}` : "Unassigned"));
  const save = async (path, payload, method = "POST") => {
    if (O_staff !== order || staffContext !== context) throw new Error("Reopen the current order before saving.");
    await api(base + path, method, {expected_version: progress.version, ...payload});
    await O_openStaff(context, order.id); notice("Registrar update saved.");
  };
  for (const item of progress.items) {
    const name = order.submitted_snapshot.items.find((i) => i.key === item.key)?.name || item.key;
    container.append(node("p", `${name}: ${item.status.replaceAll("_", " ")}`));
  }
  for (const blocker of progress.release_blockers) container.append(node("p", `Release check: ${blocker}`, "muted"));
  if (order.status === "submitted") {
    if (!progress.assigned_to || context.manager) container.append(action("Assign to me", () => save('/assignment', {user_id: currentUser.id}, 'PUT')));
    if (context.manager) F_form(container, "Reassign order", (form) => {
      O_select(form, "Registrar", "user_id", [["", "Unassigned"], ...registrars.map((r) => [String(r.id), `${r.name} (${r.role})`])], String(progress.assigned_to || ""));
    }, "Save assignment", (form) => save('/assignment', {user_id: Number(form.elements.user_id.value) || null}, 'PUT'));
    if (progress.assigned_to === currentUser.id) {
      F_form(container, "Document decision", (form) => {
        O_select(form, "Document", "key", progress.items.map((i) => [i.key, `${order.submitted_snapshot.items.find((s) => s.key === i.key)?.name || i.key} (${i.status})`]));
        const choices = [["approve", "Approve and start processing"], ["reject", "Reject during review"], ["ready", "Mark preparation ready"]];
        if (context.manager) choices.push(["reopen", "Reopen for review"]);
        O_select(form, "Decision", "decision", choices); F_notes(form);
      }, "Save document decision", (form) => save(`/items/${encodeURIComponent(form.elements.key.value)}/decisions`, {decision: form.elements.decision.value, student_message: form.elements.student_message.value, internal_note: form.elements.internal_note.value}));
      F_form(container, "Place an order hold", (form) => {
        O_select(form, "Hold category", "category", [["academic", "Academic"], ["identity", "Identity"], ["financial", "Financial"], ["administrative", "Administrative"]]); F_notes(form);
      }, "Place hold", (form) => save('/holds', {category: form.elements.category.value, student_message: form.elements.student_message.value, internal_note: form.elements.internal_note.value}));
      if (order.release_when !== "now") F_form(container, "Deferred release condition", (form) => {
        form.append(node("p", `${order.release_when.replaceAll("_", " ")}: ${order.release_instruction}`));
        O_select(form, "Confirmation", "confirmed", [["true", "Required event has occurred"], ["false", "Withdraw confirmation"]]);
        O_input(form, "Internal evidence for this confirmation", "internal_note").required = true;
      }, "Save release condition", (form) => save('/release-confirmation', {confirmed: form.elements.confirmed.value === "true", internal_note: form.elements.internal_note.value}));
    }
  }
  for (const hold of progress.holds) {
    container.append(node("p", `${hold.resolved_at ? "Resolved" : "Active"} hold: ${hold.student_message}`));
    if (!hold.resolved_at && progress.assigned_to === currentUser.id && order.status === "submitted") F_form(container, "Resolve hold", F_notes, "Resolve hold", (form) => save(`/holds/${hold.id}/resolution`, {student_message: form.elements.student_message.value, internal_note: form.elements.internal_note.value}));
  }
  if (progress.authorization) {
    const consent = document.createElement("details"); consent.append(node("summary", "Student release authorization"), node("p", progress.authorization.text), node("p", `Accepted ${new Date(progress.authorization.accepted_at).toLocaleString()} · ${progress.authorization.text_version}`)); container.append(consent);
  }
  const history = document.createElement("details"); history.append(node("summary", "Private registrar history"));
  for (const entry of progress.history) history.append(node("p", `${new Date(entry.created_at).toLocaleString()} · ${entry.action} · ${registrars.find((r) => r.id === entry.actor_id)?.name || "Former staff member"}: ${entry.internal_note}`, "history"));
  container.append(history);
}
for (const id of ["registrar-state", "registrar-mine", "registrar-holds"]) $(id).addEventListener("change", () => run(() => O_loadStaff(requireContext())));
