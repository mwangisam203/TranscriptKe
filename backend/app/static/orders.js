"use strict";
let O_checkoutPolicy = "after_review";
// The workspace owns authentication; this module never persists access tokens.
let O_current = null, O_services = [], O_quote = null, O_consentText = null;
let O_dirty = false, O_offset = 0, O_staffOffset = 0, O_staff = null, O_staffContext = null;
let O_queueGeneration = 0, O_staffGeneration = 0;
let O_createKey = null, O_submitKey = null, O_reorderKey = null, O_generation = 0;
let O_records = [];
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
function O_releaseRequirements() {
  const form = $("order-editor");
  form.elements.release_instruction.required = form.elements.release_when.value !== "now";
}
$("order-editor").elements.release_when.addEventListener("change", O_releaseRequirements);
function O_addRecipient(value = {}) {
  const section = node("fieldset"); section.dataset.key = value.key || O_key();
  section.append(node("legend", "Recipient"));
  O_input(section, "Recipient name", "recipient_name", value.name).required = true;
  const organization = O_input(section, "Receiving institution / organization", "recipient_organization", value.organization);
  const choices = [["self", "For myself"], ["institution", "Another institution"]];
  if (value.destination_type === "other") choices.push(["other", "Other recipient (existing)"]);
  const destinationType = O_select(section, "Transcript destination", "recipient_destination", choices, value.destination_type || "self"); destinationType.required = true;
  const updateDestinationType = () => {
    const self = destinationType.value === "self";
    organization.closest("label").hidden = self; organization.disabled = self;
    organization.required = destinationType.value === "institution";
    if (self) organization.value = "";
  };
  destinationType.addEventListener("change", updateDestinationType); updateDestinationType();
  O_input(section, "Recipient email", "recipient_email", value.email, "email");
  const method = O_select(section, "Delivery method", "recipient_method", [["secure_electronic", "Secure electronic"], ["collection", "Collection"], ["post", "Post"]], value.delivery_method || "secure_electronic");
  O_input(section, "Application/reference number (optional)", "recipient_reference", value.application_reference);
  const address = node("div"); section.append(address);
  for (const [key, label] of [["line1", "Address line 1"], ["line2", "Address line 2 (optional)"], ["city", "City"], ["postal_code", "Postal code"], ["country_code", "Country code (for example, KE)"]]) O_input(address, label, `address_${key}`, value.postal_address?.[key]);
  method.required = true;
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
    return {key: entry.dataset.key, destination_type: read("recipient_destination"), name: read("recipient_name"), organization: read("recipient_organization"), email: read("recipient_email") || null,
      delivery_method: method, application_reference: read("recipient_reference"), postal_address: method === "post" ? Object.fromEntries(["line1", "line2", "city", "postal_code", "country_code"].map((key) => [key, key === "country_code" ? read(`address_${key}`).toUpperCase() : read(`address_${key}`)])) : null};
  });
  const items = Array.from($("order-items").children).map((entry) => ({key: entry.dataset.key, service_id: Number(entry.querySelector("[name=item_service]").value), recipient_key: entry.querySelector("[name=item_recipient]").value, quantity: Number(entry.querySelector("[name=item_quantity]").value)}));
  return {expected_version: O_current.version, purpose: form.elements.purpose.value, release_when: form.elements.release_when.value,
    release_instruction: form.elements.release_instruction.value, recipients, items};
}
async function O_load() {
  O_checkoutPolicy = (await api("/orders/checkout-policy")).collection_policy;
  const records = []; let page;
  do { page = await api(`/me/academic-record-links?offset=${records.length}&limit=100`); records.push(...page); } while (page.length === 100);
  O_records = records; O_filterInstitutions();
  $("destination-institutions").replaceChildren(...publicInstitutions.map((item) => { const option = node("option"); option.value = item.name; return option; }));
  if (!$("new-order-self-name").value) $("new-order-self-name").value = currentUser.full_name;
  if (!$("new-order-self-email").value) $("new-order-self-email").value = currentUser.email;
  O_destination();
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
    const awaiting = order.status === "awaiting_payment";
    const status = action(awaiting ? "Checkout incomplete · Continue payment" : order.status.replaceAll("_", " "), () => O_open(order.id));
    status.classList.add("order-status-link"); status.setAttribute("aria-label", `Open ${order.reference}: ${awaiting ? "continue payment" : order.status.replaceAll("_", " ")}`);
    card.append(node("strong", order.reference), status);
    if (order.unanswered_questions) card.append(node("p", `${order.unanswered_questions} question(s) need your response.`));
    if (order.submitted_snapshot) card.append(node("p", O_money(order.submitted_snapshot.total_minor)));
    card.append(action(order.status === "draft" ? "Continue draft" : order.status === "awaiting_payment" ? "Continue payment" : "View request history", () => O_open(order.id)));
    card.append(action("Open order", () => O_open(order.id))); $("order-list").append(card);
  }
  if (!append && !orders.length) $("order-list").append(window.workspaceInterface.empty("Your next opportunity is waiting", "No orders yet. Save your enrollment details, then choose your documents and recipients.", "orders"));
  O_offset += orders.length; $("more-orders").hidden = orders.length < 30;
}
$("refresh-orders").addEventListener("click", () => run(O_load));
$("more-orders").addEventListener("click", () => run(() => O_list(true)));
function O_filterInstitutions() {
  filterInstitutions($("order-institution"), $("order-institution-search").value, $("order-institution-results")); O_recordChoices();
}
function O_recordChoices() {
  const select = $("order-record"), previous = select.value, institution = Number($("order-institution").value);
  const records = O_records.filter((record) => (record.status === "matched" || (O_checkoutPolicy === "before_review" && record.status === "pending")) && record.institution_id === institution);
  options(select, records, (record) => `${record.name_on_record} · ${record.admission_number || `ID ${record.identity_masked || "provided"}`}`, O_checkoutPolicy === "before_review" ? "Choose your enrollment details" : "Choose a confirmed academic record");
  if (records.some((record) => String(record.id) === previous)) select.value = previous;
  else if (records.length === 1) select.value = records[0].id;
  $("create-order").disabled = !records.length;
  $("order-record-help").textContent = O_checkoutPolicy === "before_review" ? (!institution ? "Choose your issuing institution." : !records.length ? "Save your enrollment details first, then choose your documents and pay at checkout." : "Choose your enrollment details. Payment comes before institution review.") : !institution ? "Select an issuing institution to see your confirmed records." : !records.length ? "No confirmed record at this institution yet. Link your record and wait for the registrar to confirm it before ordering." : "Your record has been confirmed. Choose where your documents should go.";
}
function O_destination() {
  const institution = $("new-order-destination").value === "institution";
  for (const [id, active] of [["new-order-self", !institution], ["new-order-institution", institution]]) {
    $(id).hidden = !active;
    $(id).querySelectorAll("input").forEach((input) => { input.disabled = !active; input.required = active; });
  }
}
$("order-institution-search").addEventListener("input", O_filterInstitutions);
$("order-institution").addEventListener("change", O_recordChoices);
$("new-order-destination").addEventListener("change", O_destination);
$("order-link-record").addEventListener("click", () => run(async () => {
  const id = $("order-institution").value; clearRevision(); showView("student-view"); $("student-institution").value = id;
  await loadStudentCatalog(); $("student-institution").focus();
}));
bindForm("new-order-form", async (form) => {
  if (!form.reportValidity()) return;
  const link = Number($("order-record").value), type = $("new-order-destination").value;
  if (!O_records.some((record) => record.id === link && (record.status === "matched" || (O_checkoutPolicy === "before_review" && record.status === "pending")) && record.institution_id === Number($("order-institution").value))) throw new Error("Choose eligible enrollment details at the issuing institution.");
  const institution = type === "institution", name = $(institution ? "new-order-institution-name" : "new-order-self-name").value.trim();
  const payload = {academic_record_link_id: link, recipient: {key: "destination-1", destination_type: type, name,
    organization: institution ? name : "", email: $(institution ? "new-order-institution-email" : "new-order-self-email").value.trim(), delivery_method: "secure_electronic"}};
  const fingerprint = JSON.stringify(payload);
  if (!O_createKey || O_createKey.fingerprint !== fingerprint) O_createKey = {fingerprint, key: O_key()};
  const order = await api("/orders", "POST", payload, {"Idempotency-Key": O_createKey.key});
  O_createKey = null; await window.draftsWorkspace?.clear("order-start"); await O_open(order.id); await O_list(); notice("Continue with document selection, then review consent and pay. Your unfinished checkout is saved automatically.");
});
function O_summary(container, snapshot, paymentStatus = "not_started") {
  container.replaceChildren(); if (!snapshot) return;
  container.append(node("h3", "Review your document request"), node("p", `For: ${snapshot.academic_record.name_on_record}`));
  const issuer = node("div", undefined, "card");
  issuer.append(node("p", "FROM", "eyebrow"), node("strong", snapshot.institution.name)); container.append(issuer);
  container.append(node("p", `Purpose: ${snapshot.purpose}`), node("p", `Send: ${snapshot.release_when === "now" ? "As soon as institutional processing is complete" : snapshot.release_when.replaceAll("_", " ")} ${snapshot.release_instruction}`));
  for (const recipient of snapshot.recipients) {
    const card = node("div", undefined, "card"); card.append(node("p", "TO", "eyebrow"));
    if (recipient.destination_type && recipient.destination_type !== "other") card.append(node("p", recipient.destination_type === "self" ? "For myself" : "Another institution"));
    card.append(node("strong", recipient.name), node("p", [recipient.organization, recipient.email, recipient.delivery_method.replaceAll("_", " "), recipient.application_reference].filter(Boolean).join(" · ")));
    if (recipient.postal_address) card.append(node("p", Object.values(recipient.postal_address).filter(Boolean).join(", ")));
    for (const item of snapshot.items.filter((item) => item.recipient_key === recipient.key)) card.append(node("p", `${item.name} × ${item.quantity}: ${O_money(item.line_total_minor)} · ${item.processing_days_min}–${item.processing_days_max} business days`));
    container.append(card);
  }
  container.append(node("p", `Credential fees: ${O_money(snapshot.total_minor)}. No additional tax or delivery charge is configured for these demo services.`), node("strong", `Total: ${O_money(snapshot.total_minor)}`), node("p", `Payment: ${paymentStatus.replaceAll("_", " ")}.`, "muted"));
  if (snapshot.attachments.length) container.append(node("p", `Attachments included in consent: ${snapshot.attachments.map((a) => a.filename).join(", ")}`));
}
async function O_open(id) {
  showView("orders-view");
  $("orders-view").classList.add("checkout-active");
  const generation = ++O_generation;
  $("order-editor").hidden = true;
  await window.draftsWorkspace?.flush();
  const order = await api(`/orders/${id}`);
  let services = [];
  if (order.status === "draft") {
    try { services = await api(`/institutions/${order.institution_id}/services`); } catch (error) { notice(error.message, true); }
  }
  const [timeline, messages] = await Promise.all([api(`/orders/${id}/timeline`), api(`/orders/${id}/messages`)]);
  if (generation !== O_generation) return;
  sessionStorage.setItem(`workspace-position-${currentUser.id}`, JSON.stringify({order: order.id}));
  O_current = order; O_services = services; O_quote = null; O_dirty = false; O_submitKey = null; O_reorderKey = null;
  $("order-detail").hidden = false; $("order-title").textContent = order.reference;
  $("order-state").replaceChildren(node("span", `Status: ${order.status.replaceAll("_", " ")} · `));
  $("order-state").append(action(`Payment: ${order.payment_status.replaceAll("_", " ")} · ${order.status === "awaiting_payment" ? "Continue payment" : "View payment details"}`, P_focusPayment));
  const draft = order.status === "draft";
  $("discard-order").hidden = !draft;
  O_checkoutSteps(order);
  $("order-editor").hidden = true; $("order-attachment-form").hidden = !draft; $("quote-order").hidden = !draft;
  $("order-quote").hidden = true; $("order-cancel-form").hidden = !["draft", "awaiting_payment", "submitted"].includes(order.status);
  $("reorder-button").hidden = !order.submitted_at; $("order-message-form").hidden = !order.submitted_at;
  fillForm($("order-editor"), order); O_releaseRequirements(); $("order-recipients").replaceChildren(); $("order-items").replaceChildren();
  if (draft) { order.recipients.forEach(O_addRecipient); order.items.forEach(O_addItem); await window.draftsWorkspace?.restoreOrder(order); }
  if (O_current !== order || generation !== O_generation) return;
  $("order-editor").hidden = !draft;
  O_summary($("submitted-order-summary"), order.submitted_snapshot, order.payment_status);
  O_renderAttachments($("order-attachments"), order, null);
  await F_student(order);
  if (O_current !== order) return;
  await P_student(order);
  if (O_current !== order) return;
  await D_student(order);
  if (O_current !== order) return;
  O_renderTimeline($("order-timeline"), timeline, order);
  O_renderMessages($("order-conversation"), messages);
  if (!$("orders-view").hidden) {
    $("order-detail").scrollIntoView({block: "start"});
    $("order-title").tabIndex = -1; $("order-title").focus({preventScroll: true});
    if (order.status === "awaiting_payment") P_focusPayment();
  }
  options($("order-message-form").elements.in_reply_to_id, messages.filter((m) => m.author_role === "staff" && m.requires_response && !m.answered_at), (m) => m.body, "General message");
}
async function O_save() {
  if (!O_current || O_current.status !== "draft") throw new Error("Open a draft order first.");
  if (!$("order-editor").reportValidity()) throw new Error("Complete the recipient and document details before saving.");
  const id = O_current.id;
  await api(`/orders/${id}`, "PUT", O_draftBody()); await window.draftsWorkspace?.clear(`order-${id}`); await O_open(id); await O_list();
}
bindForm("order-editor", O_review);
async function O_review() {
  await window.draftsWorkspace?.flush();
  if (O_dirty) await O_save();
  const order = O_current;
  if (!order || order.status !== "draft") return;
  const quote = await api(`/orders/${order.id}/quotes`, "POST", {expected_version: order.version});
  if (O_current !== order) return;
  O_quote = quote; O_submitKey = O_key(); O_checkoutSteps(order, true);
  const container = $("order-quote"); O_summary(container, quote.snapshot); container.hidden = false;
  container.append(node("p", `Quote expires: ${new Date(quote.expires_at).toLocaleString()}`));
  const form = document.createElement("form"), label = node("label", undefined, "check"), checkbox = document.createElement("input");
  checkbox.type = "checkbox"; checkbox.required = true; checkbox.id = "order-consent-checkbox";
  const signer = O_input(form, "Your full name *", "signer_name", currentUser.full_name);
  signer.id = "order-signer-name"; signer.required = true; signer.maxLength = 255; signer.autocomplete = "name";
  form.append(node("p", "Draw your signature below using a mouse, touch, or pen. Your signature authorizes this quoted order and its attachments."));
  form.append(node("h3", "Signature *"));
  const pad = O_signaturePad(form);
  label.append(checkbox, document.createTextNode(O_consentText.text)); form.append(label);
  form.append(node("p", order.collection_policy === "before_review" ? "Review and sign, then pay at checkout. Your institution receives the order only after confirmed payment. Paying does not confirm record ownership or guarantee issuance." : "Your institution reviews the request before payment. Card and M-Pesa options appear after registrar approval.", "muted"));
  const submit = node("button", order.collection_policy === "before_review" ? "Continue to payment" : "Authorize and submit order"); submit.id = "order-final-submit"; form.append(submit);
  let acceptedConsent = null, acceptedSignature = null;
  form.addEventListener("submit", (e) => {
    e.preventDefault(); if (submit.disabled || !form.reportValidity()) return;
    if (!pad.valid()) return notice("Draw your signature before continuing.", true);
    submit.disabled = true;
    run(async () => {
      if (O_dirty || O_quote !== quote || O_current !== order) throw new Error("The draft changed. Review a fresh quote.");
      const signaturePayload = {signer_name: signer.value.trim(), signature: pad.strokes()};
      const fingerprint = JSON.stringify(signaturePayload);
      if (acceptedConsent && acceptedSignature !== fingerprint) throw new Error("Your signature changed. Request a fresh quote and sign it again.");
      const consent = acceptedConsent ||= await api(`/orders/${order.id}/consents`, "POST", {quote_id: quote.id, text_version: O_consentText.text_version, accepted: true, ...signaturePayload});
      acceptedSignature = fingerprint;
      await api(`/orders/${order.id}/submit`, "POST", {expected_version: order.version, quote_id: quote.id, consent_id: consent.id}, {"Idempotency-Key": O_submitKey});
      await O_open(order.id); await O_list(); notice(order.collection_policy === "before_review" && quote.total_minor > 0 ? "Checkout saved privately. Complete payment below to send your order to the institution." : "Order submitted. You can track it and respond to your institution here.");
    }).finally(() => { submit.disabled = false; });
  }); container.append(form);
}
$("quote-order").addEventListener("click", () => run(O_review));

function O_signaturePad(parent) {
  const canvas = document.createElement("canvas"); canvas.id = "order-signature";
  canvas.width = 720; canvas.height = 240; canvas.className = "signature-pad";
  canvas.setAttribute("aria-label", "Draw your consent signature");
  const context = canvas.getContext("2d"); let strokes = [], active = null;
  const redraw = () => {
    context.clearRect(0, 0, canvas.width, canvas.height); context.lineWidth = 3;
    context.strokeStyle = "#171719"; context.lineCap = "round";
    for (const stroke of strokes) {
      context.beginPath(); stroke.forEach((p, i) => context[i ? "lineTo" : "moveTo"](p.x * canvas.width, p.y * canvas.height)); context.stroke();
    }
  };
  const point = (event) => {
    const rect = canvas.getBoundingClientRect();
    return {x: Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)), y: Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height))};
  };
  canvas.addEventListener("pointerdown", (event) => {
    if (active || strokes.length >= 50 || strokes.flat().length >= 1999) return;
    canvas.setPointerCapture(event.pointerId); active = [point(event)]; strokes.push(active); redraw();
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!active || strokes.flat().length >= 2000) return;
    active.push(point(event)); redraw();
  });
  const end = () => { if (active && active.length < 2) strokes.pop(); active = null; redraw(); };
  canvas.addEventListener("pointerup", end); canvas.addEventListener("pointercancel", end);
  parent.append(canvas, action("Clear signature", () => { strokes = []; active = null; redraw(); }));
  return {strokes: () => strokes, valid: () => {
    const points = strokes.flat(); if (points.length < 3) return false;
    return Math.max(...points.map(p => p.x)) - Math.min(...points.map(p => p.x)) >= 0.02 || Math.max(...points.map(p => p.y)) - Math.min(...points.map(p => p.y)) >= 0.02;
  }};
}
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
  container.replaceChildren();
  const block = node("section", undefined, "order-timeline-block");
  block.append(node("h3", "Order timeline"), node("p", "Every update stays in this one record, from saved details to delivery.", "muted"));
  const list = node("ol", undefined, "order-timeline-steps");
  for (const [index, entry] of timeline.entries()) {
    const step = node("li", undefined, "order-timeline-step");
    const number = node("span", String(index + 1), "timeline-step-number"); number.setAttribute("aria-hidden", "true");
    const details = node("div");
    const heading = node("div", undefined, "timeline-step-heading");
    const date = new Date(entry.created_at), time = node("time", date.toLocaleString()); time.dateTime = date.toISOString();
    heading.append(node("strong", entry.kind.replaceAll("_", " ")), time);
    details.append(heading, node("p", entry.message)); step.append(number, details); list.append(step);
  }
  if (!timeline.length) block.append(node("p", "Your first order update will appear here.", "muted"));
  else block.append(list);
  for (const cancellation of order.cancellations) block.append(node("p", `Cancellation ${cancellation.status}: ${cancellation.reason}${cancellation.decision_reason ? ` — ${cancellation.decision_reason}` : ""}`));
  container.append(block);
}
function O_checkoutSteps(order, reviewing = false) {
  const container = $("checkout-progress"); container.replaceChildren();
  const current = order.status === "draft" ? reviewing ? 1 : 0 : order.status === "awaiting_payment" ? 2 : 3;
  const labels = ["Documents & destination", "Review & consent", "Payment", "Order tracking"];
  const steps = node("ol", undefined, "checkout-stepper"); steps.setAttribute("aria-label", "Checkout progress");
  for (const [index, label] of labels.entries()) {
    const step = node("li", undefined, index === current ? "current" : index < current ? "complete" : "");
    if (index === current) step.setAttribute("aria-current", "step");
    step.append(node("span", String(index + 1), "checkout-step-number"), node("span", label)); steps.append(step);
  }
  container.append(steps, node("p", order.status === "draft" ? "Your unfinished checkout saves automatically. Continue one step at a time." : order.status === "awaiting_payment" ? "Complete payment to send your order to the institution." : "Your request is retained for tracking and audit."));
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
  O_summary($("staff-order-summary"), order.submitted_snapshot, order.payment_status); O_renderAttachments($("staff-order-attachments"), order, context.id);
  O_renderTimeline($("staff-order-timeline"), timeline, order); O_renderMessages($("staff-order-conversation"), messages);
  $("staff-order-cancel-form").hidden = order.status !== "cancellation_requested";
  await F_staff(context, order);
  if (O_staff !== order) return;
  await P_staff(context, order);
  if (O_staff !== order) return;
  await D_staff(context, order);
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
async function O_resumeRecord(record) {
  if (!record || !(record.status === "matched" || (O_checkoutPolicy === "before_review" && record.status === "pending"))) throw new Error("Your institution must confirm this record before ordering.");
  await window.draftsWorkspace?.flush();
  let offset = 0, page;
  do {
    page = await api(`/orders?offset=${offset}&limit=100`);
    const draft = page.find(order => ["draft", "awaiting_payment"].includes(order.status) && order.academic_record_link_id === record.id);
    if (draft) { await O_open(draft.id); return; }
    offset += page.length;
  } while (page.length === 100);
  showView("orders-view"); $("order-institution-search").value = "";
  filterInstitutions($("order-institution"), "", $("order-institution-results"));
  $("order-institution").value = record.institution_id; O_recordChoices(); $("order-record").value = record.id;
  $("new-order-form").scrollIntoView({block: "start"}); $("new-order-destination").focus({preventScroll: true});
  notice("Your enrollment details are selected. Choose the destination to continue your document request.");
}
window.ordersWorkspace = {open: O_open, load: O_load, loadStaff: O_loadStaff, resumeRecord: O_resumeRecord, reset() {
  $("orders-view").classList.remove("checkout-active");
  $("student-documents").replaceChildren(); $("staff-documents").replaceChildren();
  P_clearCard(); P_stopRefresh(); P_keys.clear(); $("student-payments").replaceChildren(); $("staff-payments").replaceChildren();
  $("student-fulfillment").replaceChildren(); $("registrar-workspace").replaceChildren();
  O_queueGeneration++; O_staffGeneration++;
  $("registrar-state").value = ""; $("registrar-mine").checked = false; $("registrar-holds").checked = false;
  O_generation++; O_current = O_quote = O_staff = O_staffContext = null; O_createKey = O_submitKey = O_reorderKey = null;
  O_records = []; O_services = []; $("order-detail").hidden = true; $("staff-order-detail").hidden = true;
  for (const id of ["order-list", "order-recipients", "order-items", "order-attachments", "order-quote", "submitted-order-summary", "order-timeline", "order-conversation", "staff-order-list", "staff-order-summary", "staff-order-attachments", "staff-order-timeline", "staff-order-conversation"]) $(id).replaceChildren();
}};

async function O_backToOrders() {
  P_clearCard(); P_stopRefresh();
  await window.draftsWorkspace?.flush();
  O_generation++; O_current = null; $("order-detail").hidden = true;
  $("orders-view").classList.remove("checkout-active");
  sessionStorage.removeItem(`workspace-position-${currentUser.id}`);
  await O_list();
}
$("checkout-back").addEventListener("click", () => run(O_backToOrders));
$("discard-order").addEventListener("click", () => run(async () => {
  const order = O_current;
  if (!order || order.status !== "draft") throw new Error("Only unfinished checkout can be deleted.");
  await window.draftsWorkspace?.flush();
  await api(`/orders/${order.id}?expected_version=${order.version}`, "DELETE");
  window.draftsWorkspace?.forget(`order-${order.id}`);
  await O_backToOrders(); notice("Unfinished checkout deleted.");
}));

$("discard-order-start").addEventListener("click", () => run(async () => {
  await window.draftsWorkspace?.clear("order-start");
  $("new-order-form").reset(); O_createKey = null; O_filterInstitutions(); O_destination();
  notice("Unfinished destination details deleted.");
}));
