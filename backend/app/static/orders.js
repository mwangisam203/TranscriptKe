"use strict";
// The workspace owns authentication; this module never persists access tokens.
let O_current = null, O_services = [], O_quote = null, O_consentText = null;
let O_dirty = false, O_offset = 0, O_staffOffset = 0, O_staff = null, O_staffContext = null;
let O_queueGeneration = 0, O_staffGeneration = 0;
let O_createKey = null, O_submitKey = null, O_reorderKey = null, O_generation = 0;
let O_attachmentPolicy = {extensions: [".txt"], max_files: 5};
const O_money = (minor) => new Intl.NumberFormat("en-KE", {style: "currency", currency: "KES"}).format(minor / 100);
const O_key = () => crypto.randomUUID();
function O_input(parent, label, name, value = "", type = "text") {
  const wrapper = node("label", label), input = document.createElement("input");
  input.name = name; input.type = type; input.value = value ?? ""; wrapper.append(input); parent.append(wrapper); return input;
}
function O_select(parent, label, name, choices, value) {
  const wrapper = node("label", label), select = document.createElement("select"); select.name = name;
  for (const [key, title] of choices) { const option = node("option", title); option.value = key; select.append(option); }
  if (value !== undefined) select.value = value;
  wrapper.append(select); parent.append(wrapper); return select;
}
function O_changed() { O_dirty = true; O_quote = null; O_submitKey = null; $("order-quote").hidden = true; }
$("order-editor").addEventListener("input", O_changed);
function O_addRecipient(value = {}) {
  const section = node("fieldset"); section.dataset.key = value.key || O_key();
  section.append(node("legend", "Recipient"));
  O_input(section, "Recipient name", "recipient_name", value.name).required = true;
  O_input(section, "Organization (optional)", "recipient_organization", value.organization);
  O_input(section, "Recipient email", "recipient_email", value.email, "email");
  const method = O_select(section, "Delivery method", "recipient_method", [["secure_electronic", "Secure electronic"], ["collection", "Collection"], ["post", "Post"]], value.delivery_method || "secure_electronic");
  O_input(section, "Application/reference number (optional)", "recipient_reference", value.application_reference);
  const address = node("div"); section.append(address);
  for (const [key, label] of [["line1", "Address line 1"], ["line2", "Address line 2 (optional)"], ["city", "City"], ["postal_code", "Postal code"], ["country_code", "Country code (for example, KE)"]]) O_input(address, label, `address_${key}`, value.postal_address?.[key]);
  const destination = () => {
    address.hidden = method.value !== "post";
    section.querySelector("[name=recipient_email]").required = method.value === "secure_electronic";
    address.querySelectorAll("input").forEach((input) => input.required = method.value === "post" && input.name !== "address_line2");
  };
  method.addEventListener("change", destination); destination();
  section.append(action("Remove recipient", () => { section.remove(); O_refreshRecipientChoices(); O_changed(); }));
  section.querySelector("[name=recipient_name]").addEventListener("input", O_refreshRecipientChoices);
  $("order-recipients").append(section); O_refreshRecipientChoices();
}
function O_recipientChoices() { return Array.from($("order-recipients").children).map((entry) => [entry.dataset.key, entry.querySelector("[name=recipient_name]").value || "Unnamed recipient"]); }
function O_refreshRecipientChoices() {
  for (const select of $("order-items").querySelectorAll("[name=item_recipient]")) {
    const previous = select.value; select.replaceChildren();
    for (const [key, title] of O_recipientChoices()) { const option = node("option", title); option.value = key; select.append(option); }
    if (previous) select.value = previous;
  }
}
function O_addItem(value = {}) {
  const section = node("fieldset"); section.dataset.key = value.key || O_key(); section.append(node("legend", "Document"));
  const serviceChoices = O_services.map((service) => [String(service.id), `${service.name} · ${O_money(service.fee_minor)} per copy`]);
  if (value.service_id && !O_services.some((service) => service.id === value.service_id)) serviceChoices.push([String(value.service_id), "Previously selected service (unavailable)"]);
  O_select(section, "Document service", "item_service", serviceChoices, value.service_id ? String(value.service_id) : undefined).required = true;
  O_select(section, "Recipient", "item_recipient", O_recipientChoices(), value.recipient_key).required = true;
  const quantity = O_input(section, "Copies", "item_quantity", value.quantity || 1, "number"); quantity.min = 1; quantity.max = 10; quantity.required = true;
  section.append(action("Remove document", () => { section.remove(); O_changed(); })); $("order-items").append(section);
}
$("add-order-recipient").addEventListener("click", () => { if ($("order-recipients").children.length >= 10) return notice("At most ten recipients are allowed.", true); O_addRecipient(); O_changed(); });
$("add-order-item").addEventListener("click", () => { if ($("order-items").children.length >= 20) return notice("At most twenty document items are allowed.", true); O_addItem(); O_changed(); });
function O_draftBody() {
  const form = $("order-editor");
  const recipients = Array.from($("order-recipients").children).map((entry) => {
    const read = (name) => entry.querySelector(`[name=${name}]`).value.trim();
    const method = read("recipient_method");
    return {key: entry.dataset.key, name: read("recipient_name"), organization: read("recipient_organization"), email: read("recipient_email") || null,
      delivery_method: method, application_reference: read("recipient_reference"), postal_address: method === "post" ? Object.fromEntries(["line1", "line2", "city", "postal_code", "country_code"].map((key) => [key, key === "country_code" ? read(`address_${key}`).toUpperCase() : read(`address_${key}`)])) : null};
  });
  const items = Array.from($("order-items").children).map((entry) => ({key: entry.dataset.key, service_id: Number(entry.querySelector("[name=item_service]").value), recipient_key: entry.querySelector("[name=item_recipient]").value, quantity: Number(entry.querySelector("[name=item_quantity]").value)}));
  return {expected_version: O_current.version, purpose: form.elements.purpose.value, release_when: form.elements.release_when.value,
    release_instruction: form.elements.release_instruction.value, recipients, items};
}
async function O_load() {
  const records = []; let page;
  do { page = await api(`/me/academic-record-links?offset=${records.length}&limit=100`); records.push(...page); } while (page.length === 100);
  options($("order-record"), records.filter((record) => record.status === "matched"), (record) => `${record.name_on_record} · ${record.admission_number}`, "Choose a confirmed academic record");
  O_attachmentPolicy = await api("/orders/attachment-policy");
  $("order-file").accept = O_attachmentPolicy.extensions.join(",");
  $("attachment-help").textContent = `Up to five files, 2 MiB each. Available formats: ${O_attachmentPolicy.extensions.join(", ")}. Only attach information relevant to this order.`;
  O_consentText = await api("/orders/consent-text"); await O_list();
}
async function O_list(append = false) {
  if (!append) { O_offset = 0; $("order-list").replaceChildren(); }
  const orders = await api(`/orders?offset=${O_offset}&limit=30`);
  for (const order of orders) {
    const card = node("article", undefined, "card");
    card.append(node("strong", order.reference), node("p", order.status.replaceAll("_", " ")));
    if (order.unanswered_questions) card.append(node("p", `${order.unanswered_questions} question(s) need your response.`));
    if (order.submitted_snapshot) card.append(node("p", O_money(order.submitted_snapshot.total_minor)));
    card.append(action("Open order", () => O_open(order.id))); $("order-list").append(card);
  }
  if (!append && !orders.length) $("order-list").append(node("p", "No orders yet. Start with a confirmed academic record."));
  O_offset += orders.length; $("more-orders").hidden = orders.length < 30;
}
$("refresh-orders").addEventListener("click", () => run(O_load));
$("more-orders").addEventListener("click", () => run(() => O_list(true)));
bindForm("new-order-form", async () => {
  const link = Number($("order-record").value);
  if (!O_createKey || O_createKey.link !== link) O_createKey = {link, key: O_key()};
  const order = await api("/orders", "POST", {academic_record_link_id: link}, {"Idempotency-Key": O_createKey.key});
  O_createKey = null; await O_open(order.id); await O_list(); notice("Draft created. Add your documents and recipients.");
});
function O_summary(container, snapshot) {
  container.replaceChildren(); if (!snapshot) return;
  container.append(node("h3", "Order details"), node("p", `${snapshot.institution.name} · ${snapshot.academic_record.name_on_record} · ${snapshot.academic_record.admission_number}`), node("p", `Purpose: ${snapshot.purpose}`), node("p", `Release: ${snapshot.release_when.replaceAll("_", " ")} ${snapshot.release_instruction}`));
  for (const recipient of snapshot.recipients) {
    const card = node("div", undefined, "card");
    card.append(node("strong", recipient.name), node("p", [recipient.organization, recipient.email, recipient.delivery_method.replaceAll("_", " "), recipient.application_reference].filter(Boolean).join(" · ")));
    if (recipient.postal_address) card.append(node("p", Object.values(recipient.postal_address).filter(Boolean).join(", ")));
    for (const item of snapshot.items.filter((item) => item.recipient_key === recipient.key)) card.append(node("p", `${item.name} × ${item.quantity}: ${O_money(item.line_total_minor)} · ${item.processing_days_min}–${item.processing_days_max} business days`));
    container.append(card);
  }
  container.append(node("strong", `Total: ${O_money(snapshot.total_minor)}`), node("p", "Payment has not been collected.", "muted"));
  if (snapshot.attachments.length) container.append(node("p", `Attachments included in consent: ${snapshot.attachments.map((a) => a.filename).join(", ")}`));
}
async function O_open(id) {
  const generation = ++O_generation;
  const order = await api(`/orders/${id}`);
  let services = [];
  if (order.status === "draft") {
    try { services = await api(`/institutions/${order.institution_id}/services`); } catch (error) { notice(error.message, true); }
  }
  const [timeline, messages] = await Promise.all([api(`/orders/${id}/timeline`), api(`/orders/${id}/messages`)]);
  if (generation !== O_generation) return;
  O_current = order; O_services = services; O_quote = null; O_dirty = false; O_submitKey = null; O_reorderKey = null;
  $("order-detail").hidden = false; $("order-title").textContent = order.reference;
  $("order-state").textContent = `Status: ${order.status.replaceAll("_", " ")} · Payment: ${order.payment_status.replaceAll("_", " ")}`;
  const draft = order.status === "draft";
  $("order-editor").hidden = !draft; $("order-attachment-form").hidden = !draft; $("quote-order").hidden = !draft;
  $("order-quote").hidden = true; $("order-cancel-form").hidden = !["draft", "submitted"].includes(order.status);
  $("reorder-button").hidden = !order.submitted_at; $("order-message-form").hidden = !order.submitted_at;
  fillForm($("order-editor"), order); $("order-recipients").replaceChildren(); $("order-items").replaceChildren();
  if (draft) { order.recipients.forEach(O_addRecipient); order.items.forEach(O_addItem); }
  O_summary($("submitted-order-summary"), order.submitted_snapshot);
  O_renderAttachments($("order-attachments"), order, null);
  await F_student(order);
  if (O_current !== order) return;
  O_renderTimeline($("order-timeline"), timeline, order);
  O_renderMessages($("order-conversation"), messages);
  options($("order-message-form").elements.in_reply_to_id, messages.filter((m) => m.author_role === "staff" && m.requires_response && !m.answered_at), (m) => m.body, "General message");
}
async function O_save() {
  if (!O_current || O_current.status !== "draft") throw new Error("Open a draft order first.");
  if (!$("order-editor").reportValidity()) throw new Error("Complete the recipient and document details before saving.");
  const id = O_current.id;
  await api(`/orders/${id}`, "PUT", O_draftBody()); await O_open(id); await O_list();
}
bindForm("order-editor", async () => { await O_save(); notice("Draft saved."); });
$("quote-order").addEventListener("click", () => run(async () => {
  if (O_dirty) await O_save();
  const order = O_current;
  if (!order || order.status !== "draft") return;
  const quote = await api(`/orders/${order.id}/quotes`, "POST", {expected_version: order.version});
  if (O_current !== order) return;
  O_quote = quote; O_submitKey = O_key();
  const container = $("order-quote"); O_summary(container, quote.snapshot); container.hidden = false;
  container.append(node("p", `Quote expires: ${new Date(quote.expires_at).toLocaleString()}`));
  const form = document.createElement("form"), label = node("label", undefined, "check"), checkbox = document.createElement("input");
  checkbox.type = "checkbox"; checkbox.required = true; checkbox.id = "order-consent-checkbox";
  label.append(checkbox, document.createTextNode(O_consentText.text)); form.append(label);
  const submit = node("button", "Authorize and submit order"); submit.id = "order-final-submit"; form.append(submit);
  let acceptedConsent = null;
  form.addEventListener("submit", (e) => {
    e.preventDefault(); if (submit.disabled) return; submit.disabled = true;
    run(async () => {
      if (O_dirty || O_quote !== quote || O_current !== order) throw new Error("The draft changed. Review a fresh quote.");
      const consent = acceptedConsent ||= await api(`/orders/${order.id}/consents`, "POST", {quote_id: quote.id, text_version: O_consentText.text_version, accepted: true});
      await api(`/orders/${order.id}/submit`, "POST", {expected_version: order.version, quote_id: quote.id, consent_id: consent.id}, {"Idempotency-Key": O_submitKey});
      await O_open(order.id); await O_list(); notice("Order submitted. You can track it and respond to your institution here.");
    }).finally(() => { submit.disabled = false; });
  }); container.append(form);
}));
async function O_download(path, filename) {
  const generation = sessionGeneration;
  const response = await fetch(`/api/v1${path}`, {headers: {Authorization: `Bearer ${accessToken}`}, cache: "no-store"});
  if (!response.ok || generation !== sessionGeneration) throw new Error("Attachment unavailable or your session expired.");
  const blob = await response.blob(); if (generation !== sessionGeneration) return;
  const url = URL.createObjectURL(blob), anchor = document.createElement("a"); anchor.href = url; anchor.download = filename;
  anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function O_renderAttachments(container, order, institutionId) {
  container.replaceChildren();
  for (const attachment of order.attachments) {
    const row = node("div", undefined, "card"); row.append(node("span", `${attachment.filename} · ${attachment.size} bytes`));
    const prefix = institutionId ? `/staff/institutions/${institutionId}/orders/${order.id}` : `/orders/${order.id}`;
    row.append(action("Download attachment", () => O_download(`${prefix}/attachments/${attachment.id}/download`, attachment.filename)));
    if (order.status === "draft" && !institutionId) row.append(action("Remove attachment", async () => {
      if (O_dirty) throw new Error("Save your draft changes before removing an attachment.");
      await api(`${prefix}/attachments/${attachment.id}?expected_version=${order.version}`, "DELETE"); await O_open(order.id);
    })); container.append(row);
  }
  if (!order.attachments.length) container.append(node("p", "No supporting attachments.", "muted"));
}
bindForm("order-attachment-form", async (form) => {
  if (O_dirty) throw new Error("Save your draft changes before adding an attachment.");
  const order = O_current, data = new FormData(form); data.append("expected_version", order.version);
  await api(`/orders/${order.id}/attachments`, "POST", data); form.reset(); await O_open(order.id); notice("Attachment added. Review a fresh quote before submitting.");
});
function O_renderTimeline(container, timeline, order) {
  container.replaceChildren(node("h3", "Order timeline"));
  for (const entry of timeline) { const row = node("div", undefined, "history"); row.append(node("strong", `${entry.kind.replaceAll("_", " ")} · ${new Date(entry.created_at).toLocaleString()}`), node("p", entry.message)); container.append(row); }
  for (const cancellation of order.cancellations) container.append(node("p", `Cancellation ${cancellation.status}: ${cancellation.reason}${cancellation.decision_reason ? ` — ${cancellation.decision_reason}` : ""}`));
}
function O_renderMessages(container, messages) {
  container.replaceChildren(node("h3", "Messages"));
  for (const message of messages) {
    const row = node("div", undefined, "history"); row.append(node("strong", `${message.author_role} · ${new Date(message.created_at).toLocaleString()}`), node("p", message.body));
    if (message.requires_response) row.append(node("p", message.answered_at ? "Response received." : "Student response needed.")); container.append(row);
  }
}
bindForm("order-message-form", async (form) => {
  const order = O_current; await api(`/orders/${order.id}/messages`, "POST", {body: form.elements.body.value, in_reply_to_id: Number(form.elements.in_reply_to_id.value) || null});
  form.reset(); await O_open(order.id); await O_list(); notice("Message sent.");
});
bindForm("order-cancel-form", async (form) => {
  const order = O_current; await api(`/orders/${order.id}/cancellation-requests`, "POST", {expected_version: order.version, reason: form.elements.reason.value});
  form.reset(); await O_open(order.id); await O_list(); notice("Cancellation recorded. Submitted orders require an institutional decision.");
});
$("reorder-button").addEventListener("click", () => run(async () => {
  const order = O_current; O_reorderKey ||= O_key();
  const draft = await api(`/orders/${order.id}/reorder`, "POST", undefined, {"Idempotency-Key": O_reorderKey});
  await O_open(draft.id); await O_list(); notice("Repeat draft created. Check current services and prices, then give fresh consent.");
}));
async function O_loadStaff(context, append = false) {
  if (!context) return;
  const generation = ++O_queueGeneration;
  if (!append) O_staffGeneration++;
  if (!append) { O_staffOffset = 0; O_staff = null; $("staff-order-detail").hidden = true; $("staff-order-list").replaceChildren(); }
  O_staffContext = context;
  const query = new URLSearchParams({offset: O_staffOffset, limit: 30});
  if ($("registrar-state").value) query.set("state", $("registrar-state").value);
  if ($("registrar-mine").checked) query.set("assigned_to", currentUser.id);
  if ($("registrar-holds").checked) query.set("on_hold", "true");
  const orders = await api(`/staff/institutions/${context.id}/registrar-queue?${query}`);
  if (generation !== O_queueGeneration || O_staffContext !== context || staffContext !== context) return;
  for (const order of orders) {
    const row = node("div", undefined, "card"); row.append(node("strong", order.reference), node("p", order.status.replaceAll("_", " ")), action("Review order", () => O_openStaff(context, order.id))); $("staff-order-list").append(row);
  }
  if (!append && !orders.length) $("staff-order-list").append(node("p", "No submitted document orders yet."));
  O_staffOffset += orders.length; $("more-staff-orders").hidden = orders.length < 30;
}
async function O_openStaff(context, id) {
  const generation = ++O_staffGeneration;
  const base = `/staff/institutions/${context.id}/orders/${id}`;
  const [order, timeline, messages] = await Promise.all([api(base), api(`${base}/timeline`), api(`${base}/messages`)]);
  if (generation !== O_staffGeneration || staffContext !== context || O_staffContext !== context) return;
  O_staff = order; $("staff-order-detail").hidden = false; $("staff-order-title").textContent = `${order.reference} · ${order.status.replaceAll("_", " ")}`;
  O_summary($("staff-order-summary"), order.submitted_snapshot); O_renderAttachments($("staff-order-attachments"), order, context.id);
  O_renderTimeline($("staff-order-timeline"), timeline, order); O_renderMessages($("staff-order-conversation"), messages);
  $("staff-order-cancel-form").hidden = order.status !== "cancellation_requested";
  await F_staff(context, order);
}
$("refresh-staff-orders").addEventListener("click", () => run(() => O_loadStaff(requireContext())));
$("more-staff-orders").addEventListener("click", () => run(() => O_loadStaff(requireContext(), true)));
bindForm("staff-order-message-form", async (form) => {
  const order = O_staff, context = requireContext(); if (!order || order.institution_id !== context.id) throw new Error("Open an order at this institution first.");
  await api(`/staff/institutions/${context.id}/orders/${order.id}/messages`, "POST", {body: form.elements.body.value, requires_response: form.elements.requires_response.checked});
  form.reset(); await O_openStaff(context, order.id); notice("Student message sent.");
});
bindForm("staff-order-cancel-form", async (form) => {
  const order = O_staff, context = requireContext(); if (!order || order.institution_id !== context.id) throw new Error("Open an order at this institution first.");
  await api(`/staff/institutions/${context.id}/orders/${order.id}/cancellation-decisions`, "POST", {expected_version: order.version, decision: form.elements.decision.value, reason: form.elements.reason.value});
  form.reset(); await O_loadStaff(context); await O_openStaff(context, order.id); notice("Cancellation decision saved.");
});
window.ordersWorkspace = {load: O_load, loadStaff: O_loadStaff, reset() {
  $("student-fulfillment").replaceChildren(); $("registrar-workspace").replaceChildren();
  O_queueGeneration++; O_staffGeneration++;
  $("registrar-state").value = ""; $("registrar-mine").checked = false; $("registrar-holds").checked = false;
  O_generation++; O_current = O_quote = O_staff = O_staffContext = null; O_createKey = O_submitKey = O_reorderKey = null;
  O_services = []; $("order-detail").hidden = true; $("staff-order-detail").hidden = true;
  for (const id of ["order-list", "order-recipients", "order-items", "order-attachments", "order-quote", "submitted-order-summary", "order-timeline", "order-conversation", "staff-order-list", "staff-order-summary", "staff-order-attachments", "staff-order-timeline", "staff-order-conversation"]) $(id).replaceChildren();
}};
