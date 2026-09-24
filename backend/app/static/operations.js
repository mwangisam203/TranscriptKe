"use strict";
let OP_generation = 0, OP_items = [], OP_kind = "overdue", OP_context = null;
const OP_labels = {all: "All submitted orders", overdue: "Past planning target", assignment_required: "Assignment needed", holds: "Active holds", awaiting_student: "Awaiting student response", payments: "Payment exceptions", deliveries: "Delivery exceptions", cancellations: "Cancellation requests", ready: "Ready documents", follow_up: "Follow-ups due", missing_target: "Missing planning target"};
function OP_date(value) {return value ? `${new Date(value).toLocaleString(undefined, {timeZone: "Africa/Nairobi"})} EAT` : "Not available";}
function OP_reset() {
  OP_generation++; OP_context = null; OP_items = []; OP_kind = "overdue"; $("operations-kind").value = OP_kind;
  $("operations-panel").hidden = true; $("operations-summary").replaceChildren(); $("operations-queue").replaceChildren(); $("operations-case").replaceChildren();
}
async function OP_load(context, append = false) {
  if (!context.manager) {OP_reset(); return;}
  const generation = ++OP_generation;
  OP_context = context; $("operations-panel").hidden = false;
  if (!append) {OP_items = []; $("operations-case").replaceChildren();}
  const kind = OP_kind, base = `/staff/institutions/${context.id}/operations`;
  const [summary, page] = await Promise.all([api(base + "/summary"), api(`${base}/queue?kind=${kind}&offset=${OP_items.length}&limit=30`)]);
  if (generation !== OP_generation || staffContext !== context || OP_context !== context) return;
  const overview = $("operations-summary"); overview.replaceChildren(node("p", `Submitted orders: ${summary.counts.submitted_total} · Open fulfillment: ${summary.counts.open_fulfillment}`));
  for (const [key, label] of Object.entries(OP_labels)) if (key !== "all") overview.append(action(`${label}: ${summary.counts[key]}`, async () => {OP_kind = key; $("operations-kind").value = key; await OP_load(context);}));
  overview.append(node("p", summary.timing_policy, "muted"), node("p", `As of ${OP_date(summary.as_of)}`, "muted"));
  OP_items.push(...page.items);
  const queue = $("operations-queue"); queue.replaceChildren(node("h3", `${OP_labels[kind]} (${page.total})`));
  if (!OP_items.length) queue.append(node("p", "No orders currently match this queue."));
  for (const item of OP_items) {
    const card = node("div", undefined, "card");
    card.append(node("h4", item.reference), node("p", `Order: ${item.status.replaceAll("_", " ")} · Payment: ${item.payment_status.replaceAll("_", " ")}`), node("p", `Planning target: ${OP_date(item.processing_due_at)}`));
    const labels = item.flags.filter((key) => key in OP_labels).map((key) => OP_labels[key]);
    if (labels.length) card.append(node("p", labels.join(" · ")));
    if (item.release_when !== "now") card.append(node("p", `Deferred release: ${item.release_when.replaceAll("_", " ")}. The planning clock does not pause.`, "muted"));
    card.append(action("Open order workflow", async () => {
      if (staffContext !== context) throw new Error("Select this institution again.");
      await O_openStaff(context, item.id); if (staffContext === context) $("staff-order-detail").scrollIntoView({block: "start"});
    }), action("Review follow-up", () => OP_case(context, item.id)));
    queue.append(card);
  }
  $("operations-more").hidden = OP_items.length >= page.total;
}
async function OP_case(context, orderId) {
  const generation = ++OP_generation;
  const base = `/staff/institutions/${context.id}/operations/orders/${orderId}`;
  const result = await api(base);
  if (generation !== OP_generation || staffContext !== context || OP_context !== context) return;
  const container = $("operations-case"); container.replaceChildren(node("h3", `Private operations review · ${result.reference}`));
  F_form(container, "Save a follow-up", (form) => {
    const note = O_input(form, "Private manager note", "note"); note.required = true; note.maxLength = 2000; note.value = result.note;
    const due = O_input(form, "Follow-up (your local time; leave blank to clear)", "follow_up_at"); due.type = "datetime-local";
    if (result.follow_up_at) {const date = new Date(result.follow_up_at); due.value = new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);}
  }, "Save operations review", async (form) => {
    if (generation !== OP_generation || staffContext !== context || OP_context !== context) throw new Error("Reopen this operations review before saving.");
    await api(base, "PUT", {expected_version: result.version, note: form.elements.note.value, follow_up_at: form.elements.follow_up_at.value ? new Date(form.elements.follow_up_at.value).toISOString() : null});
    await OP_load(context); notice("Operations review saved. Existing payment and release checks still apply.");
  });
  if (result.history.length) {
    const history = node("details"); history.append(node("summary", "Recent private operations history (up to 50 entries)"));
    for (const entry of result.history) history.append(node("p", `${OP_date(entry.created_at)} · ${entry.note}`, "history"));
    container.append(history);
  }
  container.scrollIntoView({block: "nearest"});
}
for (const [key, label] of Object.entries(OP_labels)) {const option = node("option", label); option.value = key; $("operations-kind").append(option);}
$("operations-kind").value = OP_kind;
$("operations-kind").addEventListener("change", () => run(async () => {OP_kind = $("operations-kind").value; await OP_load(requireContext());}));
$("operations-refresh").addEventListener("click", () => run(() => OP_load(requireContext())));
$("operations-more").addEventListener("click", () => run(() => OP_load(requireContext(), true)));
window.operationsWorkspace = {load: OP_load, reset: OP_reset};
