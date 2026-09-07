"use strict";
const $ = (id) => document.getElementById(id);
const fields = {program: "Program", attendance_start_year: "First attendance year", attendance_end_year: "Last attendance year", previous_names: "Previous names (or explicitly none)"};
let accessToken = "", sessionGeneration = 0, currentUser = null;
let memberships = [], publicInstitutions = [], studentServices = [], studentPolicy = null;
let studentLoad = 0, staffLoad = 0, staffContext = null, editingService = null, revisingLink = null, reviewingLink = null;
let adminInstitutions = [], linksOffset = 0, matchesOffset = 0;

function notice(message, error = false) { $("notice").textContent = message; $("notice").classList.toggle("error", error); }
function node(tag, content, className) { const element = document.createElement(tag); if (content !== undefined) element.textContent = content; if (className) element.className = className; return element; }
function action(label, fn) { const button = node("button", label, "secondary"); button.type = "button"; button.addEventListener("click", () => run(fn)); return button; }
async function run(fn) { try { await fn(); } catch (error) { notice(error.message, true); } }
function bindForm(id, fn) {
  $(id).addEventListener("submit", (event) => {
    event.preventDefault();
    const button = event.submitter;
    if (button?.disabled) return;
    if (button) button.disabled = true;
    run(() => fn($(id))).finally(() => { if (button) button.disabled = false; if (id === "record-form") updateRequirements(); });
  });
}
function signOutView() {
  sessionGeneration++; accessToken = ""; currentUser = null; staffContext = null;
  memberships = []; studentServices = []; publicInstitutions = []; adminInstitutions = [];
  editingService = revisingLink = reviewingLink = null;
  studentLoad++; staffLoad++;
  document.querySelectorAll("form").forEach((form) => form.reset());
  for (const id of ["my-links", "student-history", "match-list", "review-history", "review-details", "service-list"]) $(id).replaceChildren();
  $("workspace").hidden = true; $("authentication").hidden = false; $("logout").hidden = true;
}
async function api(path, method = "GET", body) {
  const generation = sessionGeneration;
  const headers = {"Content-Type": "application/json"};
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
  const response = await fetch(`/api/v1${path}`, {method, headers, body: body === undefined ? undefined : JSON.stringify(body), cache: "no-store"});
  if (generation !== sessionGeneration) throw new Error("The session changed. Please try again.");
  if (response.status === 401 && accessToken) signOutView();
  const data = response.status === 204 ? null : await response.json();
  if (!response.ok) {
    let detail = data?.detail;
    if (Array.isArray(detail)) detail = detail.map((item) => `${item.loc.slice(1).join(" ")}: ${item.msg}`).join("\n");
    else if (detail && typeof detail === "object") detail = `${detail.message}: ${(detail.fields || []).map((key) => fields[key] || key).join(", ")}`;
    throw new Error(detail || "The request could not be completed.");
  }
  return data;
}
function formObject(form) { return Object.fromEntries(new FormData(form)); }
function options(select, items, label, placeholder) {
  select.replaceChildren();
  if (placeholder) { const option = node("option", placeholder); option.value = ""; select.append(option); }
  for (const item of items) { const option = node("option", label(item)); option.value = item.id; select.append(option); }
}
function checkboxes(container, name) {
  for (const [value, label] of Object.entries(fields)) {
    const item = node("label", undefined, "check"), input = document.createElement("input");
    input.type = "checkbox"; input.name = name; input.value = value;
    item.append(input, document.createTextNode(label)); $(container).append(item);
  }
}
checkboxes("policy-required", "required_fields"); checkboxes("service-required", "required_fields");
function fillForm(form, data) {
  for (const element of form.elements) {
    if (!element.name || !(element.name in data)) continue;
    const value = data[element.name];
    if (element.type === "checkbox") element.checked = Array.isArray(value) ? value.includes(element.value) : Boolean(value);
    else element.value = value ?? "";
  }
}
function showView(id) {
  for (const view of ["student-view", "staff-view", "admin-view"]) $(view).hidden = view !== id;
  document.querySelectorAll("[data-view]").forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.view === id)));
}
document.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", () => showView(button.dataset.view)));

async function loadSession() {
  currentUser = await api("/auth/me");
  memberships = await api("/staff/memberships");
  publicInstitutions = await api("/institutions");
  $("welcome").textContent = `Welcome, ${currentUser.full_name}`;
  $("staff-tab").hidden = memberships.length === 0;
  $("admin-tab").hidden = currentUser.role !== "admin";
  options($("student-institution"), publicInstitutions, (item) => item.name, "Choose an institution");
  // Memberships may belong to an institution awaiting approval, so fetch its scoped details.
  const staffInstitutions = await Promise.all(memberships.map((membership) => api(`/staff/institutions/${membership.institution_id}`)));
  options($("staff-institution"), staffInstitutions, (item) => item.name);
  if (currentUser.role === "admin") {
    adminInstitutions = [];
    let page;
    do { page = await api(`/admin/institutions?offset=${adminInstitutions.length}`); adminInstitutions.push(...page); } while (page.length === 100);
    options($("admin-institution"), adminInstitutions, (item) => item.name);
    showApproval();
  }
  $("authentication").hidden = true; $("workspace").hidden = false; $("logout").hidden = false;
  clearRevision(); showView("student-view"); await loadLinks();
  if (memberships.length) await loadStaff();
}
bindForm("login-form", async (form) => { const data = await api("/auth/login", "POST", formObject(form)); accessToken = data.access_token; form.reset(); await loadSession(); notice("Signed in."); });
bindForm("register-form", async (form) => { await api("/auth/register", "POST", formObject(form)); form.reset(); notice("Account created. Check your email for a verification code before signing in."); });
bindForm("verify-form", async (form) => { const data = await api("/auth/email-verifications/confirm", "POST", formObject(form)); form.reset(); notice(data.message); });
bindForm("resend-form", async (form) => notice((await api("/auth/email-verifications", "POST", formObject(form))).message));
bindForm("reset-request-form", async (form) => notice((await api("/auth/password-reset-requests", "POST", formObject(form))).message));
bindForm("reset-form", async (form) => { const data = await api("/auth/password-resets", "POST", formObject(form)); form.reset(); notice(data.message); });
$("logout").addEventListener("click", () => run(async () => { await api("/auth/logout", "POST"); signOutView(); notice("Signed out."); }));
bindForm("accept-invitation-form", async (form) => { await api("/staff/invitations/accept", "POST", formObject(form)); form.reset(); await loadSession(); notice("Invitation accepted. Your institution workspace is ready."); });

async function loadStudentCatalog() {
  const generation = ++studentLoad, id = $("student-institution").value;
  $("student-service").replaceChildren(); studentServices = []; studentPolicy = null;
  $("record-submit").disabled = true; $("service-summary").textContent = ""; $("ordering-instructions").textContent = "";
  if (!id) return;
  const [services, policy] = await Promise.all([api(`/institutions/${id}/services`), api(`/institutions/${id}/ordering-policy`)]);
  if (generation !== studentLoad) return;
  studentServices = services; studentPolicy = policy;
  options($("student-service"), services, (item) => item.name, "Choose a document service");
  $("ordering-instructions").textContent = policy.student_instructions;
  if (!services.length) $("service-summary").textContent = "No services are available yet.";
  if (!policy.accepting_requests) $("service-summary").textContent = "This institution is not accepting new submissions at present.";
  updateRequirements();
}
function updateRequirements() {
  const service = studentServices.find((item) => item.id === Number($("student-service").value));
  const required = new Set([...(studentPolicy?.required_fields || []), ...(service?.required_fields || [])]);
  const form = $("record-form");
  for (const field of ["program", "attendance_start_year", "attendance_end_year"]) form.elements[field].required = required.has(field);
  $("program-requirement").textContent = required.has("program") ? "(required)" : "(optional)";
  $("names-requirement").textContent = required.has("previous_names") ? "(required; leave blank if none)" : "(optional)";
  if (service) $("service-summary").textContent = `${new Intl.NumberFormat("en-KE", {style: "currency", currency: service.currency}).format(service.fee_minor / 100)} · ${service.processing_days_min}–${service.processing_days_max} business days · ${service.delivery_methods.map((value) => value.replaceAll("_", " ")).join(", ")}. No payment is collected now.${studentPolicy?.accepting_requests ? "" : " Submissions are currently closed."}`;
  $("record-submit").disabled = !service || !studentPolicy?.accepting_requests;
}
$("student-institution").addEventListener("change", () => run(loadStudentCatalog));
$("student-service").addEventListener("change", updateRequirements);
function clearRevision() {
  revisingLink = null; $("record-form").reset(); $("student-institution").disabled = false;
  $("record-form-title").textContent = "Link an academic record"; $("record-submit").textContent = "Submit for matching";
  $("cancel-resubmit").hidden = true; studentServices = []; studentPolicy = null;
  $("student-service").replaceChildren(); $("service-summary").textContent = ""; $("ordering-instructions").textContent = "";
  $("record-submit").disabled = true;
}
$("cancel-resubmit").addEventListener("click", clearRevision);
bindForm("record-form", async (form) => {
  const values = formObject(form);
  const payload = {service_id: Number(values.service_id), admission_number: values.admission_number,
    name_on_record: values.name_on_record, program: values.program.trim() || null,
    attendance_start_year: values.attendance_start_year ? Number(values.attendance_start_year) : null,
    attendance_end_year: values.attendance_end_year ? Number(values.attendance_end_year) : null,
    previous_names: values.previous_names.split("\n").map((value) => value.trim()).filter(Boolean)};
  if (revisingLink) await api(`/me/academic-record-links/${revisingLink.id}/resubmissions`, "POST", {...payload, expected_version: revisingLink.version});
  else await api("/me/academic-record-links", "POST", {...payload, institution_id: Number(values.institution_id)});
  clearRevision(); await loadLinks(); notice("Your details have been submitted for institutional review.");
});
function recordCard(link) {
  const card = node("article", undefined, "card");
  card.append(node("strong", `${link.name_on_record} · ${link.admission_number}`), node("p", `${link.program || "Program not specified"} · ${link.requirements_snapshot.service_name}`), node("span", link.status.replaceAll("_", " "), `badge ${link.status}`));
  if (link.student_message) card.append(node("p", link.student_message));
  return card;
}
async function loadLinks(append = false) {
  if (!append) { linksOffset = 0; $("my-links").replaceChildren(); $("student-history").replaceChildren(); }
  const links = await api(`/me/academic-record-links?offset=${linksOffset}&limit=50`);
  for (const link of links) {
    const card = recordCard(link);
    const institution = publicInstitutions.find((item) => item.id === link.institution_id);
    card.append(node("p", institution?.name || "Institution currently unavailable", "muted"));
    card.append(action("View history", async () => renderHistory($("student-history"), await api(`/me/academic-record-links/${link.id}/events`), false)));
    if (["needs_information", "rejected"].includes(link.status) && institution) card.append(action("Update details", async () => {
      revisingLink = await api(`/me/academic-record-links/${link.id}`);
      $("student-institution").value = revisingLink.institution_id; $("student-institution").disabled = true;
      await loadStudentCatalog();
      fillForm($("record-form"), {...revisingLink, previous_names: revisingLink.previous_names.join("\n")});
      updateRequirements(); $("record-form-title").textContent = "Update your matching details";
      $("record-submit").textContent = "Resubmit for matching"; $("cancel-resubmit").hidden = false;
      $("record-form").scrollIntoView({behavior: "smooth", block: "start"});
    }));
    $("my-links").append(card);
  }
  if (!links.length && !append) $("my-links").append(node("p", "You have no academic record links yet.", "muted"));
  linksOffset += links.length; $("more-links").hidden = links.length < 50;
}
$("refresh-links").addEventListener("click", () => run(() => loadLinks()));
$("more-links").addEventListener("click", () => run(() => loadLinks(true)));
function renderHistory(container, events, staff) {
  container.replaceChildren(node("h3", "Review history"));
  for (const event of events) {
    const item = node("div", undefined, "history");
    item.append(node("strong", `${event.status.replaceAll("_", " ")} · ${new Date(event.created_at).toLocaleString()}`));
    if (event.student_message) item.append(node("p", event.student_message));
    const snapshot = event.submission_snapshot;
    item.append(node("p", `${snapshot.name_on_record} · ${snapshot.admission_number} · ${snapshot.program || "No program supplied"}`, "muted"));
    if (staff && event.internal_note) item.append(node("p", `Private evidence: ${event.internal_note}`));
    if (staff && event.record_reference) item.append(node("p", `Institutional reference: ${event.record_reference}`));
    container.append(item);
  }
}

async function loadStaff() {
  const generation = ++staffLoad, id = Number($("staff-institution").value);
  staffContext = null; reviewingLink = null; editingService = null;
  $("review-area").hidden = true; $("manager-tools").hidden = true; $("match-list").replaceChildren();
  if (!id) return;
  const [institution, services, policy] = await Promise.all([api(`/staff/institutions/${id}`), api(`/staff/institutions/${id}/services`), api(`/staff/institutions/${id}/ordering-policy`)]);
  if (generation !== staffLoad) return;
  const manager = memberships.some((item) => item.institution_id === id && item.role === "manager");
  staffContext = {id, services, policy, manager};
  $("staff-institution-status").textContent = `${institution.name} · ${institution.is_approved ? "Approved institution" : "Awaiting platform approval"}`;
  $("manager-tools").hidden = !manager;
  fillForm($("policy-form"), policy); renderServices(); resetServiceForm(); await loadMatches();
}
$("staff-institution").addEventListener("change", () => run(loadStaff));
function requireContext() { if (!staffContext) throw new Error("Select an institution and wait for it to load."); return staffContext; }
async function loadMatches(append = false) {
  const context = requireContext(), filter = $("match-filter").value;
  if (!append) { matchesOffset = 0; $("match-list").replaceChildren(); reviewingLink = null; $("review-area").hidden = true; }
  const links = await api(`/staff/institutions/${context.id}/record-matches?offset=${matchesOffset}&limit=50${filter ? `&status=${filter}` : ""}`);
  if (staffContext !== context || $("match-filter").value !== filter) return;
  for (const link of links) {
    const card = recordCard(link);
    card.append(action("Open review", () => openReview(context, link.id)));
    $("match-list").append(card);
  }
  if (!links.length && !append) $("match-list").append(node("p", "No matching requests in this view.", "muted"));
  matchesOffset += links.length; $("more-matches").hidden = links.length < 50;
}
$("match-filter").addEventListener("change", () => run(() => loadMatches()));
$("refresh-matches").addEventListener("click", () => run(() => loadMatches()));
$("more-matches").addEventListener("click", () => run(() => loadMatches(true)));
async function openReview(context, id) {
  const base = `/staff/institutions/${context.id}/record-matches/${id}`;
  const [link, events] = await Promise.all([api(base), api(`${base}/events`)]);
  if (staffContext !== context) return;
  reviewingLink = link; $("review-area").hidden = false;
  $("review-title").textContent = `Review ${link.name_on_record}`;
  $("review-details").replaceChildren(recordCard(link), node("p", `Attendance: ${link.attendance_start_year || "Not supplied"}–${link.attendance_end_year || "Not supplied"}. Previous names: ${link.previous_names.join(", ") || "None supplied"}.`));
  renderHistory($("review-history"), events, true);
  const canReview = link.user_id !== currentUser.id && (link.status === "pending" || (context.manager && ["matched", "rejected"].includes(link.status)));
  $("decision-form").hidden = !canReview; $("decision-form").reset();
  for (const option of $("decision-form").elements.decision.options) option.disabled = link.status !== "pending" && option.value !== "needs_information";
  updateDecisionFields();
  if (link.status !== "pending" && canReview) $("review-details").append(node("p", "Requesting more information reopens this decision and removes its confirmed status until the student resubmits and staff review it again."));
}
function updateDecisionFields() {
  const form = $("decision-form"), matching = form.elements.decision.value === "matched";
  $("ownership-fields").hidden = !matching;
  form.elements.record_reference.required = matching; form.elements.ownership_confirmed.required = matching;
  form.elements.internal_note.required = matching; form.elements.internal_note.minLength = matching ? 20 : 0;
}
$("decision-form").elements.decision.addEventListener("change", updateDecisionFields);
bindForm("decision-form", async (form) => {
  const context = requireContext(), link = reviewingLink;
  if (!link || link.institution_id !== context.id) throw new Error("Open a matching request before deciding.");
  const values = formObject(form), matching = values.decision === "matched";
  await api(`/staff/institutions/${context.id}/record-matches/${link.id}/decisions`, "POST", {
    expected_version: link.version, decision: values.decision, student_message: values.student_message,
    record_reference: matching ? values.record_reference : null, internal_note: values.internal_note.trim() || null,
    ownership_confirmed: matching && form.elements.ownership_confirmed.checked,
  });
  if (staffContext !== context) return;
  await loadMatches(); notice("Decision saved. The student can view your message.");
});
bindForm("policy-form", async (form) => {
  const context = requireContext(), data = new FormData(form);
  const updated = await api(`/staff/institutions/${context.id}/ordering-policy`, "PUT", {
    expected_version: context.policy.version, accepting_requests: form.elements.accepting_requests.checked,
    student_instructions: data.get("student_instructions"), required_fields: data.getAll("required_fields"),
  });
  if (staffContext === context) context.policy = updated;
  notice("Institution policy saved.");
});
function resetServiceForm() { editingService = null; $("service-form").reset(); $("service-form-title").textContent = "New service"; }
$("new-service").addEventListener("click", resetServiceForm);
function renderServices() {
  $("service-list").replaceChildren();
  for (const service of requireContext().services) {
    const card = node("div", undefined, "card");
    card.append(node("strong", `${service.name} · ${service.is_active ? "Available" : "Unavailable"}`), action("Edit service", () => {
      editingService = service; fillForm($("service-form"), {...service, fee: (service.fee_minor / 100).toFixed(2)});
      $("service-form-title").textContent = `Edit ${service.name}`;
    })); $("service-list").append(card);
  }
}
bindForm("service-form", async (form) => {
  const context = requireContext(), editing = editingService, data = new FormData(form);
  const fee = String(data.get("fee"));
  if (!/^\d+(\.\d{1,2})?$/.test(fee)) throw new Error("Enter a nonnegative fee with up to two decimal places.");
  const [whole, fraction = ""] = fee.split(".");
  const payload = {code: data.get("code"), name: data.get("name"), document_type: data.get("document_type"),
    description: data.get("description"), fee_minor: Number(whole) * 100 + Number(fraction.padEnd(2, "0")), currency: "KES",
    processing_days_min: Number(data.get("processing_days_min")), processing_days_max: Number(data.get("processing_days_max")),
    delivery_methods: data.getAll("delivery_methods"), required_fields: data.getAll("required_fields"), is_active: form.elements.is_active.checked};
  const base = `/staff/institutions/${context.id}/services`;
  await api(editing ? `${base}/${editing.id}` : base, editing ? "PUT" : "POST", editing ? {...payload, expected_version: editing.version} : payload);
  if (staffContext === context) await loadStaff();
  notice("Document service saved.");
});
bindForm("staff-invite-form", async (form) => { await api(`/staff/institutions/${requireContext().id}/invitations`, "POST", {...formObject(form), role: "staff"}); form.reset(); notice("Staff invitation sent."); });
function showApproval() {
  const institution = adminInstitutions.find((item) => item.id === Number($("admin-institution").value));
  $("approval-form").hidden = !institution; $("admin-invite-form").hidden = !institution;
  if (!institution) return;
  $("approval-status").textContent = `${institution.is_approved ? "Approved" : "Awaiting approval"} · ${institution.is_active ? "Active" : "Inactive"}`;
  $("approval-form").reset(); $("approval-form").elements.approved.checked = institution.is_approved;
}
$("admin-institution").addEventListener("change", showApproval);
bindForm("approval-form", async (form) => {
  const institution = adminInstitutions.find((item) => item.id === Number($("admin-institution").value));
  if (!institution) throw new Error("Choose an institution.");
  const updated = await api(`/admin/institutions/${institution.id}/approval`, "PUT", {
    approved: form.elements.approved.checked, expected_approved: institution.is_approved, reason: form.elements.reason.value,
  });
  Object.assign(institution, updated); showApproval();
  publicInstitutions = await api("/institutions"); options($("student-institution"), publicInstitutions, (item) => item.name, "Choose an institution"); clearRevision();
  notice("Institution approval saved.");
});
bindForm("admin-invite-form", async (form) => {
  const id = $("admin-institution").value; if (!id) throw new Error("Choose an institution.");
  await api(`/staff/institutions/${id}/invitations`, "POST", formObject(form)); form.reset(); notice("Institution invitation sent.");
});
for (const name of ["attendance_start_year", "attendance_end_year"]) $("record-form").elements[name].max = new Date().getFullYear();
