"use strict";
const $ = (id) => document.getElementById(id);
const fields = {program: "Program", attendance_start_year: "Attendance start (month and year)", attendance_end_year: "Attendance end (month and year)", previous_names: "Previous names (or explicitly none)", identity_images: "ID or driving licence images (front and back)"};
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
  stopSessionRenewal();
  window.financeWorkspace?.reset();
  window.notificationsWorkspace?.reset();
  window.workspaceInterface?.reset();
  window.pilotWorkspace?.reset();
  window.ordersWorkspace?.reset();
  window.operationsWorkspace?.reset();
  window.profileWorkspace?.reset();
  if (currentUser) sessionStorage.removeItem(`workspace-position-${currentUser.id}`);
  window.draftsWorkspace?.reset();
  sessionGeneration++; accessToken = ""; currentUser = null; staffContext = null;
  memberships = []; studentServices = []; publicInstitutions = []; adminInstitutions = [];
  editingService = revisingLink = reviewingLink = null;
  studentLoad++; staffLoad++;
  document.querySelectorAll("form").forEach((form) => form.reset());
  for (const id of ["my-links", "student-history", "match-list", "review-history", "review-details", "service-list"]) $(id).replaceChildren();
  $("workspace").hidden = true; $("authentication").hidden = false; $("logout").hidden = true; $("header-account").hidden = true;
}
async function api(path, method = "GET", body, extraHeaders = {}) {
  const generation = sessionGeneration;
  if (accessToken && currentUser) await renewActiveSession().catch(() => {});
  if (generation !== sessionGeneration) throw new Error("The session changed. Sign in again to continue.");
  const sentToken = accessToken;
  const multipart = body instanceof FormData;
  const headers = {...(multipart ? {} : {"Content-Type": "application/json"}), ...extraHeaders};
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
  const response = await fetch(`/api/v1${path}`, {method, headers, body: body === undefined ? undefined : multipart ? body : JSON.stringify(body), cache: "no-store"});
  if (generation !== sessionGeneration) throw new Error("The session changed. Please try again.");
  if (response.status === 401 && accessToken && sentToken === accessToken) {
    signOutView(); notice("Your session expired or was revoked. Sign in again; your saved checkout details are retained.", true);
  }
  let data = null;
  if (response.status !== 204) {
    if (response.headers.get("Content-Type")?.includes("application/json")) {
      try { data = await response.json(); }
      catch { throw new Error(`The server returned an unreadable response (HTTP ${response.status}). Please try again.`); }
    } else if (response.ok) {
      throw new Error("The server returned an unexpected response. Please refresh and try again.");
    }
  }
  if (!response.ok) {
    let detail = data?.detail;
    if (Array.isArray(detail)) detail = detail.map((item) => `${item.loc.slice(1).join(" ")}: ${item.msg}`).join("\n");
    else if (detail && typeof detail === "object") detail = `${detail.message}: ${(detail.blockers || detail.fields || []).map((key) => fields[key] || key).join(", ")}`;
    const fallback = response.status >= 500
      ? `The server could not complete this request (HTTP ${response.status}). Please try again; if it continues, check the backend terminal.`
      : response.status === 404 ? "The requested page or resource was not found (HTTP 404)." : "The request could not be completed.";
    throw new Error(detail || fallback);
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
  if (typeof P_clearCard === "function") { P_clearCard(); P_stopRefresh(); }
  for (const view of ["student-view", "orders-view", "account-view", "staff-view", "admin-view"]) $(view).hidden = view !== id;
  window.workspaceInterface?.view(id);
  document.querySelectorAll("nav [data-view]").forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.view === id)));
}
document.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", () => {
  if (button.dataset.view === "orders-view") $("orders-view").classList.remove("checkout-active");
  showView(button.dataset.view);
}));

async function loadSession() {
  currentUser = await api("/auth/me");
  startSessionRenewal();
  memberships = await api("/staff/memberships");
  publicInstitutions = await api("/institutions");
  $("welcome").textContent = `Welcome back, ${currentUser.full_name.trim().split(/\s+/)[0]}.`;
  window.workspaceInterface?.profile();
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
  $("authentication").hidden = true; $("workspace").hidden = false; $("logout").hidden = false; $("header-account").hidden = false;
  window.workspaceInterface?.public(false);
  O_checkoutPolicy = (await api("/orders/checkout-policy")).collection_policy;
  $("record-submit").textContent = O_checkoutPolicy === "before_review" ? "Continue to documents and destination" : "Submit for matching";
  clearRevision(); showView("student-view"); await loadLinks();
  if (memberships.length) await loadStaff();
  await window.ordersWorkspace?.load();
  await window.profileWorkspace?.load();
  await window.draftsWorkspace?.load();
  window.workspaceInterface?.entered();
  window.notificationsWorkspace?.load();
}
bindForm("login-form", async (form) => { const data = await api("/auth/login", "POST", formObject(form)); accessToken = data.access_token; form.reset(); await loadSession(); notice("Signed in."); });
bindForm("register-form", async (form) => {
  const email = form.elements.email.value;
  const payload = window.profileWorkspace.registrationPayload(form);
  await api("/auth/register", "POST", payload); form.reset();
  $("login-form").elements.email.value = email; $("resend-form").elements.email.value = email;
  window.workspaceInterface?.auth("verify");
  notice(document.querySelector(".development-mail-hint") ? "Account created. Local email mode: your verification code is in backend/.mailbox/ (or your configured mail directory), not your inbox." : "Account created. Check your email and spam folder for the single-use verification code before signing in.");
});
bindForm("verify-form", async (form) => { const data = await api("/auth/email-verifications/confirm", "POST", formObject(form)); form.reset(); window.workspaceInterface?.auth("login"); notice(data.message); });
bindForm("resend-form", async (form) => notice((await api("/auth/email-verifications", "POST", formObject(form))).message));
bindForm("reset-request-form", async (form) => {notice((await api("/auth/password-reset-requests", "POST", formObject(form))).message); $("reset-code-details").open = true; $("reset-form").elements.token.focus();});
bindForm("reset-form", async (form) => { const data = await api("/auth/password-resets", "POST", formObject(form)); form.reset(); window.workspaceInterface?.auth("login"); notice(data.message); });
bindForm("change-password-form", async (form) => {
  const values = formObject(form);
  if (values.password !== values.confirm_password) throw new Error("The new passwords do not match.");
  const data = await api("/auth/password", "PUT", {current_password: values.current_password, password: values.password});
  const email = currentUser.email;
  signOutView(); $("login-form").elements.email.value = email;
  notice(data.message); $("authentication").scrollIntoView({block: "start"});
});
$("account-password-recovery").addEventListener("click", () => run(async () => {
  const email = currentUser.email;
  await api("/auth/logout", "POST"); signOutView();
  window.workspaceInterface?.auth("recover");
  $("reset-request-form").elements.email.value = email;
  $("authentication").scrollIntoView({block: "start"});
  notice("Enter your email to request a password reset code.");
}));
$("logout").addEventListener("click", () => run(async () => { await api("/auth/logout", "POST"); signOutView(); notice("Signed out."); }));
bindForm("accept-invitation-form", async (form) => { await api("/staff/invitations/accept", "POST", formObject(form)); form.reset(); await loadSession(); notice("Invitation accepted. Your institution workspace is ready."); });

async function loadStudentCatalog() {
  const generation = ++studentLoad, id = $("student-institution").value;
  $("student-service").replaceChildren(); studentServices = []; studentPolicy = null;
  $("record-submit").disabled = true; $("service-summary").textContent = ""; $("ordering-instructions").textContent = "";
  updateIdentityImages();
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
  for (const field of ["program", "attendance_start_year", "attendance_end_year"]) form.elements[field.replace("_year", "_date")].required = required.has(field);
  const legacyDates = [];
  for (const side of ["start", "end"]) {
    if (revisingLink?.[`attendance_${side}_year`] && !revisingLink[`attendance_${side}_month`]) {
      form.elements[`attendance_${side}_date`].required = true;
      legacyDates.push(`${side}: ${revisingLink[`attendance_${side}_year`]}`);
    }
  }
  $("attendance-date-help").textContent = legacyDates.length ? `Previously recorded years (${legacyDates.join(", ")}). Type the correct year and month as YYYY-MM when updating these details.` : "Type the year and month as YYYY-MM (for example 2020-09).";
  $("program-requirement").textContent = required.has("program") ? "(required)" : "(optional)";
  $("names-requirement").textContent = required.has("previous_names") ? "(required; leave blank if none)" : "(optional)";
  if (service) $("service-summary").textContent = `${new Intl.NumberFormat("en-KE", {style: "currency", currency: service.currency}).format(service.fee_minor / 100)} · ${service.processing_days_min}–${service.processing_days_max} business days · ${service.delivery_methods.map((value) => value.replaceAll("_", " ")).join(", ")}. No payment is collected now.${studentPolicy?.accepting_requests ? "" : " Submissions are currently closed."}`;
  const current = $("record-currently-enrolled").value === "yes";
  const endDate = form.elements.attendance_end_date;
  endDate.disabled = current; endDate.required = !current && ($("record-currently-enrolled").value === "no" || required.has("attendance_end_year") || endDate.required);
  if (current && required.has("attendance_end_year")) $("attendance-date-help").textContent = "This service requires completed attendance dates. If you are still attending, choose another service or contact your institution.";
  updateIdentityImages();
  $("record-submit").disabled = !service || !studentPolicy?.accepting_requests || (current && required.has("attendance_end_year"));
  if (revisingLink && !["needs_information", "rejected"].includes(revisingLink.status)) {
    for (const input of form.querySelectorAll("input, select, textarea")) input.disabled = true;
    $("record-submit").disabled = true;
  }
}
function filterInstitutions(select, query, status) {
  const previous = select.value, term = query.trim().toLowerCase();
  const matches = publicInstitutions.filter((item) => institutionMatches(item, term));
  options(select, matches, (item) => item.name, "Choose an institution");
  if (matches.some((item) => String(item.id) === previous)) select.value = previous;
  status.textContent = `${matches.length} institution${matches.length === 1 ? "" : "s"} found`;
  renderInstitutionSuggestions(select, matches, query);
}
$("student-institution-search").addEventListener("input", () => {
  if (revisingLink) return;
  const previous = $("student-institution").value;
  filterInstitutions($("student-institution"), $("student-institution-search").value, $("student-institution-results"));
  if (previous !== $("student-institution").value) { clearIdentityUploads(); run(loadStudentCatalog); }
});
function clearIdentityUploads() {
  $("identity-pair-error").hidden = true; $("identity-pair-error").textContent = "";
  $("record-include-images").checked = false;
  for (const side of ["front", "back"]) $("record-form").elements[`identity_${side}`].value = "";
}
$("student-institution").addEventListener("change", () => { clearIdentityUploads(); run(loadStudentCatalog); });
$("student-service").addEventListener("change", updateRequirements);
$("record-currently-enrolled").addEventListener("change", updateRequirements);
function updateIdentityImages() {
  const service = studentServices.find((item) => item.id === Number($("student-service").value));
  const required = new Set([...(studentPolicy?.required_fields || []), ...(service?.required_fields || [])]).has("identity_images");
  const saved = revisingLink?.identity_images?.length === 2, include = $("record-include-images");
  if (required && !saved) include.checked = true;
  include.disabled = required && !saved;
  $("identity-image-requirement").textContent = saved ? "Both images are already on file. Leave this unchecked to retain them, or upload both sides to replace them." : required ? "This institution requires front and back images of your ID or driving licence." : "Provide images only if your school asks for them.";
  $("record-image-fields").hidden = !include.checked;
  for (const name of ["identity_document_type", "identity_front", "identity_back"]) {
    const input = $("record-form").elements[name]; input.disabled = !include.checked; input.required = include.checked;
    if (!include.checked && input.type === "file") input.value = "";
  }
}
$("record-include-images").addEventListener("change", updateIdentityImages);
function updateLookupMethod() {
  const identity = $("record-lookup-method").value === "identity";
  $("admission-lookup-field").hidden = identity; $("identity-lookup-field").hidden = !identity;
  const form = $("record-form");
  form.elements.id_number_type.disabled = !identity;
  updateIdNumberType();
  form.elements.admission_number.disabled = identity; form.elements.admission_number.required = !identity;
  form.elements.id_number.disabled = !identity; form.elements.id_number.required = identity;
  if (identity) form.elements.admission_number.value = ""; else form.elements.id_number.value = "";
}
function updateIdNumberType() {
  const input = $("record-form").elements.id_number, national = $("record-id-number-type").value === "national_id";
  input.minLength = national ? 7 : 4; input.maxLength = national ? 8 : 32;
  input.pattern = national ? "[0-9]{7,8}" : "[A-Za-z0-9-]+";
  input.inputMode = national ? "numeric" : "text";
}
$("record-id-number-type").addEventListener("change", updateIdNumberType);
$("record-lookup-method").addEventListener("change", updateLookupMethod);
function clearRevision() {
  $("discard-enrollment-draft").hidden = false;
  $("identity-pair-error").hidden = true; $("identity-pair-error").textContent = "";
  studentLoad++;
  for (const input of $("record-form").querySelectorAll("input, select, textarea")) input.disabled = false;
  $("record-continue-order").hidden = true; $("record-submit").hidden = false;
  $("record-form").elements.id_number.placeholder = "";
  $("cancel-resubmit").textContent = "Cancel revision";
  revisingLink = null; $("record-form").reset(); $("student-institution").disabled = false; $("student-institution-search").disabled = false;
  filterInstitutions($("student-institution"), "", $("student-institution-results"));
  $("record-form-title").textContent = "Link an academic record"; $("record-submit").textContent = O_checkoutPolicy === "before_review" ? "Continue to documents and destination" : "Submit for matching";
  studentServices = []; studentPolicy = null; $("record-form").elements.attendance_end_date.disabled = false; updateLookupMethod(); updateIdentityImages();
  $("cancel-resubmit").hidden = true;
  $("student-service").replaceChildren(); $("service-summary").textContent = ""; $("ordering-instructions").textContent = "";
  window.profileWorkspace?.prefillEnrollment();
  $("record-submit").disabled = true; $("attendance-date-help").textContent = "Type the year and month as YYYY-MM (for example 2020-09).";
}
$("cancel-resubmit").addEventListener("click", clearRevision);
bindForm("record-form", async (form) => {
  if (revisingLink && !["needs_information", "rejected"].includes(revisingLink.status)) throw new Error("This enrollment is already under review or confirmed.");
  const values = formObject(form);
  const payload = {service_id: Number(values.service_id), admission_number: values.admission_number?.trim() || null, id_number: values.id_number?.trim() || null,
    id_number_type: values.id_number_type || "national_id",
    currently_enrolled: values.currently_enrolled === "yes",
    name_on_record: values.name_on_record, program: values.program.trim() || null,
    attendance_start_year: values.attendance_start_date ? Number(values.attendance_start_date.split("-")[0]) : null,
    attendance_start_month: values.attendance_start_date ? Number(values.attendance_start_date.split("-")[1]) : null,
    attendance_end_year: values.attendance_end_date ? Number(values.attendance_end_date.split("-")[0]) : null,
    attendance_end_month: values.attendance_end_date ? Number(values.attendance_end_date.split("-")[1]) : null,
    previous_names: values.previous_names.split("\n").map((value) => value.trim()).filter(Boolean)};
  if (!form.reportValidity()) return;
  for (const name of ["attendance_start_date", "attendance_end_date"]) {
    if (values[name] && !/^\d{4}-\d{2}$/.test(values[name])) throw new Error("Use month and year in YYYY-MM format for attendance dates.");
  }
  const revision = revisingLink;
  const details = revision ? {...payload, expected_version: revision.version} : {...payload, institution_id: Number(values.institution_id)};
  let savedLink;
  if ($("record-include-images").checked) {
    details.identity_document_type = values.identity_document_type;
    const data = new FormData(); data.append("details", JSON.stringify(details));
    for (const side of ["front", "back"]) {
      const file = form.elements[`identity_${side}`].files[0];
      if (!file || file.size > 2 * 1024 * 1024) throw new Error("Provide both ID images, up to 2 MiB each.");
      data.append(side, file);
    }
    if (!(await checkIdentityPair(form))) throw new Error("Upload different photographs for front and back.");
    savedLink = await api(revision ? `/me/academic-record-links/${revision.id}/resubmissions-with-images` : "/me/academic-record-submissions", "POST", data);
  } else savedLink = await api(revision ? `/me/academic-record-links/${revision.id}/resubmissions` : "/me/academic-record-links", "POST", details);
  if (!revision) await window.draftsWorkspace?.clear("enrollment");
  clearRevision(); await loadLinks();
  if (!revision && savedLink.checkout_required) {
    await window.ordersWorkspace.load(); await window.ordersWorkspace.resumeRecord(savedLink);
    notice("Enrollment saved privately. Choose your documents and destination, then review, sign and pay to send your request to the institution.");
  } else notice("Your enrollment details have been saved. Open your document request for the next step.");
});
function recordCard(link) {
  const card = node("article", undefined, "card");
  card.append(node("strong", `${link.name_on_record} · ${link.admission_number || `ID ${link.identity_masked || "provided"}`}`), node("p", `${link.program || "Program not specified"} · ${link.requirements_snapshot.service_name}`), node("span", link.status.replaceAll("_", " "), `badge ${link.status}`));
  card.append(node("p", `Attendance: ${attendanceLabel(link, "start")} – ${attendanceLabel(link, "end")}`));
  if (link.currently_enrolled !== null) card.append(node("p", link.currently_enrolled ? "Currently enrolled" : "No longer attending"));
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
    if (link.status === "pending") card.append(node("p", link.checkout_required ? "Enrollment saved privately. Choose your documents and complete payment to send the request to your institution." : "Enrollment review pending. Open your document request for the next step.", "muted"));
    if ((link.status === "matched" || (link.status === "pending" && O_checkoutPolicy === "before_review")) && institution) card.append(action("Continue to document request", () => window.ordersWorkspace.resumeRecord(link)));
    identityImageControls(card, link, `/me/academic-record-links/${link.id}`);
    card.append(action("View history", async () => {
      const history = await api(`/me/academic-record-links/${link.id}/events`);
      await openEnrollmentProcess(link.id); renderHistory($("student-history"), history, false);
    }));
    if (["needs_information", "rejected"].includes(link.status) && institution) card.append(action("Update details", () => openEnrollmentProcess(link.id)));
    $("my-links").append(card);
  }
  if (!links.length && !append) $("my-links").append(window.workspaceInterface.empty("Your story starts here", "You have no academic record links yet. Link a record using the form, and your institution will review it."));
  linksOffset += links.length; $("more-links").hidden = links.length < 50;
  if (!append) await window.workspaceInterface?.overview();
}
$("refresh-links").addEventListener("click", () => run(() => loadLinks()));
$("more-links").addEventListener("click", () => run(() => loadLinks(true)));
async function openEnrollmentProcess(id) {
  await window.draftsWorkspace?.flush(); clearRevision();
  const link = await api(`/me/academic-record-links/${id}`); revisingLink = link;
  $("discard-enrollment-draft").hidden = true;
  showView("student-view");
  $("student-institution-search").value = ""; filterInstitutions($("student-institution"), "", $("student-institution-results"));
  $("student-institution-search").disabled = true; $("student-institution").value = link.institution_id; $("student-institution").disabled = true;
  $("record-include-images").checked = false;
  await loadStudentCatalog(); if (revisingLink !== link) return;
  fillForm($("record-form"), {...link, id_number_type: link.id_number_type || "national_id", attendance_start_date: attendanceInput(link, "start"), attendance_end_date: attendanceInput(link, "end"), previous_names: link.previous_names.join("\n")});
  $("record-currently-enrolled").value = link.currently_enrolled === true ? "yes" : link.currently_enrolled === false ? "no" : "";
  $("record-lookup-method").value = link.admission_number ? "admission" : "identity"; updateLookupMethod();
  const editable = ["needs_information", "rejected"].includes(link.status);
  $("record-form").elements.id_number.placeholder = link.identity_masked ? editable ? "Re-enter your ID/passport number" : `Saved ID: ${link.identity_masked}` : "";
  $("record-form-title").textContent = editable ? "Continue your enrollment details" : link.status === "matched" ? "Your enrollment is confirmed" : link.checkout_required ? "Your enrollment details are saved privately" : "Your enrollment is being reviewed";
  $("record-submit").textContent = "Resubmit for matching"; $("record-submit").hidden = !editable;
  $("record-continue-order").hidden = !(link.status === "matched" || (link.status === "pending" && O_checkoutPolicy === "before_review"));
  $("cancel-resubmit").hidden = false; $("cancel-resubmit").textContent = "Back to new enrollment";
  updateRequirements();
  if (!editable) $("attendance-date-help").textContent = link.status === "matched" ? "Continue to your saved draft or start a document request using this confirmed record." : link.checkout_required ? "Continue to choose documents, review consent and pay. Your institution receives the request after confirmed payment." : "These details are saved. Wait for your registrar’s review or request for more information.";
  $("record-form").scrollIntoView({block: "start"}); $("record-form-title").tabIndex = -1; $("record-form-title").focus({preventScroll: true});
}
$("record-continue-order").addEventListener("click", () => run(() => window.ordersWorkspace.resumeRecord(revisingLink)));

function renderHistory(container, events, staff) {
  container.replaceChildren(node("h3", "Review history"));
  for (const event of events) {
    const item = node("div", undefined, "history");
    item.append(node("strong", `${event.status.replaceAll("_", " ")} · ${new Date(event.created_at).toLocaleString()}`));
    if (event.student_message) item.append(node("p", event.student_message));
    const snapshot = event.submission_snapshot;
    item.append(node("p", `${snapshot.name_on_record} · ${snapshot.admission_number || `ID ${snapshot.identity_masked || "provided"}`} · ${snapshot.program || "No program supplied"}`, "muted"));
    if (staff && event.internal_note) item.append(node("p", `Private evidence: ${event.internal_note}`));
    if (staff && event.record_reference) item.append(node("p", `Institutional reference: ${event.record_reference}`));
    container.append(item);
  }
}

async function loadStaff() {
  const generation = ++staffLoad, id = Number($("staff-institution").value);
  staffContext = null; reviewingLink = null; editingService = null;
  window.pilotWorkspace?.reset("staff");
  window.operationsWorkspace?.reset();
  window.profileWorkspace?.reset();
  $("review-area").hidden = true; $("manager-tools").hidden = true; $("match-list").replaceChildren();
  if (!id) return;
  const [institution, services, policy] = await Promise.all([api(`/staff/institutions/${id}`), api(`/staff/institutions/${id}/services`), api(`/staff/institutions/${id}/ordering-policy`)]);
  if (generation !== staffLoad) return;
  const manager = memberships.some((item) => item.institution_id === id && item.role === "manager");
  const context = {id, services, policy, manager};
  staffContext = context;
  $("staff-institution-status").textContent = `${institution.name} · ${institution.is_approved ? "Approved institution" : "Awaiting platform approval"}`;
  $("manager-tools").hidden = !manager;
  fillForm($("policy-form"), policy); renderServices(); resetServiceForm(); await loadMatches();
  if (generation !== staffLoad || staffContext !== context) return;
  await window.ordersWorkspace?.loadStaff(context);
  if (generation === staffLoad && staffContext === context) await window.operationsWorkspace?.load(context);
  if (generation === staffLoad && staffContext === context) await window.pilotWorkspace?.loadStaff(context);
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
  if (!links.length && !append) $("match-list").append(window.workspaceInterface.empty("You’re all caught up", "No matching requests in this view. Choose another status or refresh to check for new requests.", "check"));
  matchesOffset += links.length; $("more-matches").hidden = links.length < 50;
}
$("match-filter").addEventListener("change", () => run(() => loadMatches()));
$("refresh-matches").addEventListener("click", () => run(() => loadMatches()));
$("more-matches").addEventListener("click", () => run(() => loadMatches(true)));
function identityImageControls(container, link, base) {
  if (!link.identity_images?.length) return;
  container.append(node("p", `${link.identity_document_type === "driving_licence" ? "Driving licence" : "National ID"} images provided (private).`));
  for (const image of link.identity_images) container.append(action(`Download ID ${image.side}`, async () => {
    const generation = sessionGeneration;
    const response = await fetch(`/api/v1${base}/identity-images/${image.side}`, {headers: {Authorization: `Bearer ${accessToken}`}, cache: "no-store"});
    if (!response.ok) throw new Error("Identity image is unavailable or access was denied.");
    const blob = await response.blob();
    if (generation !== sessionGeneration) return;
    const url = URL.createObjectURL(blob), anchor = document.createElement("a");
    anchor.href = url; anchor.download = `${link.identity_document_type}-${image.side}.jpg`; anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }));
}
async function openReview(context, id) {
  const base = `/staff/institutions/${context.id}/record-matches/${id}`;
  const [link, events] = await Promise.all([api(base), api(`${base}/events`)]);
  if (staffContext !== context) return;
  reviewingLink = link; $("review-area").hidden = false;
  $("review-title").textContent = `Review ${link.name_on_record}`;
  $("review-details").replaceChildren(recordCard(link), node("p", `Attendance: ${attendanceLabel(link, "start")} – ${attendanceLabel(link, "end")}. Previous names: ${link.previous_names.join(", ") || "None supplied"}.`));
  identityImageControls($("review-details"), link, base);
  if (link.identity_masked) {
    const identity = node("p", `ID/passport: ${link.identity_masked}`);
    const reveal = action("View ID for institutional matching", async () => {
      const context = staffContext, selected = reviewingLink;
      const data = await api(`/staff/institutions/${link.institution_id}/record-matches/${link.id}/identity`);
      if (staffContext !== context || reviewingLink !== selected) return;
      identity.textContent = `ID/passport: ${data.id_number}`; reveal.remove();
    });
    $("review-details").append(identity, reveal);
  }
  window.profileWorkspace?.staffDetails($("review-details"), link, base);
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
  window.pilotWorkspace?.reset("admin");
  const institution = adminInstitutions.find((item) => item.id === Number($("admin-institution").value));
  $("approval-form").hidden = !institution; $("admin-invite-form").hidden = !institution;
  if (!institution) return;
  $("approval-status").textContent = `${institution.is_approved ? "Approved" : "Awaiting approval"} · ${institution.is_active ? "Active" : "Inactive"}`;
  $("approval-form").reset(); $("approval-form").elements.approved.checked = institution.is_approved;
  run(() => window.financeWorkspace?.school(institution.id));
  run(() => window.financeWorkspace?.load());
  run(() => window.pilotWorkspace?.loadAdmin(institution.id));
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
function attendanceInput(link, side) {
  const year = link[`attendance_${side}_year`], month = link[`attendance_${side}_month`];
  return year && month ? `${year}-${String(month).padStart(2, "0")}` : "";
}
function attendanceLabel(link, side) {
  const year = link[`attendance_${side}_year`], month = link[`attendance_${side}_month`];
  if (!year) return side === "end" ? "Not supplied / still attending" : "Not supplied";
  return month ? new Intl.DateTimeFormat("en", {month: "short", year: "numeric", timeZone: "UTC"}).format(new Date(Date.UTC(year, month - 1, 1))) : `${year} (month not recorded)`;
}
const attendanceNow = new Date();
for (const name of ["attendance_start_date", "attendance_end_date"]) $("record-form").elements[name].max = `${attendanceNow.getFullYear()}-${String(attendanceNow.getMonth() + 1).padStart(2, "0")}`;

function institutionMatches(item, query) {
  const name = `${item.name} ${item.code}`.toLowerCase();
  return query.trim().toLowerCase().split(/\s+/).filter(Boolean).every((part) => name.includes(part));
}
function renderInstitutionSuggestions(select, matches, query) {
  const prefix = select.id === "student-institution" ? "student" : "order";
  const results = $(`${prefix}-institution-suggestions`), search = $(`${prefix}-institution-search`);
  results.replaceChildren(); results.hidden = !query.trim();
  if (results.hidden) return;
  if (!matches.length) results.append(node("p", "No matching institutions. Try another part of the name.", "muted"));
  for (const item of matches.slice(0, 20)) {
    const entry = node("div"); entry.setAttribute("role", "listitem");
    const button = action(item.name, () => {
      select.value = item.id; search.value = item.name; results.hidden = true;
      select.dispatchEvent(new Event("change", {bubbles: true}));
      select.focus();
    });
    button.append(node("small", item.code)); entry.append(button); results.append(entry);
  }
  if (matches.length > 20) results.append(node("p", "Keep typing to narrow these results.", "muted"));
}
for (const prefix of ["student", "order"]) {
  const input = $(`${prefix}-institution-search`), results = $(`${prefix}-institution-suggestions`);
  $(`${prefix}-institution`).addEventListener("change", () => { results.hidden = true; });
  const wrapper = input.closest(".institution-search");
  wrapper.addEventListener("focusout", event => { if (!wrapper.contains(event.relatedTarget)) results.hidden = true; });
  document.addEventListener("pointerdown", event => { if (!wrapper.contains(event.target)) results.hidden = true; });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Escape") { results.hidden = true; return; }
    if (results.hidden || !["ArrowDown", "ArrowUp", "Enter"].includes(event.key)) return;
    event.preventDefault();
    const buttons = results.querySelectorAll("button");
    if (event.key === "Enter") buttons[0]?.click();
    else (event.key === "ArrowUp" ? buttons[buttons.length - 1] : buttons[0])?.focus();
  });
  results.addEventListener("keydown", (event) => {
    if (event.key === "Escape") { results.hidden = true; input.focus(); return; }
    if (!["ArrowDown", "ArrowUp"].includes(event.key)) return;
    event.preventDefault(); const buttons = Array.from(results.querySelectorAll("button"));
    const next = buttons.indexOf(event.target) + (event.key === "ArrowDown" ? 1 : -1);
    if (next < 0 || next >= buttons.length) input.focus(); else buttons[next].focus();
  });
}
let directoryInstitutions = [], directoryOpener = null, directoryGeneration = 0;
function renderDirectory() {
  const matches = directoryInstitutions.filter((item) => institutionMatches(item, $("directory-search").value));
  $("directory-status").textContent = `${matches.length} institution${matches.length === 1 ? "" : "s"} found`;
  const list = $("directory-list"); list.replaceChildren();
  for (const item of matches) {
    const card = node("article", undefined, "card");
    card.append(node("h3", item.name), node("p", `${item.code} · ${item.country}`));
    if (item.code.startsWith("DEMO-")) card.append(node("span", "Demo institution", "badge"));
    list.append(card);
  }
  if (!matches.length) list.append(node("p", "No institutions match. Try a shorter name or clear the search.", "muted"));
}
for (const trigger of document.querySelectorAll("[data-institution-directory]")) trigger.addEventListener("click", (event) => {
  event.preventDefault(); directoryOpener = trigger;
  run(async () => {
    const generation = ++directoryGeneration;
    $("institution-directory").hidden = false; $("directory-status").textContent = "Loading institutions…";
    $("directory-list").replaceChildren(); $("directory-search").value = "";
    $("institution-directory").scrollIntoView({block: "start"});
    try {
      const institutions = await api("/institutions");
      if (generation !== directoryGeneration) return;
      directoryInstitutions = institutions; renderDirectory(); $("directory-search").focus({preventScroll: true});
    } catch (error) {
      if (generation === directoryGeneration) $("directory-status").textContent = "Could not load institutions. Close and reopen the directory to retry.";
      throw error;
    }
  });
});
$("directory-search").addEventListener("input", renderDirectory);
$("close-institution-directory").addEventListener("click", () => {
  directoryGeneration++; $("institution-directory").hidden = true; directoryOpener?.focus();
});

window.addEventListener("DOMContentLoaded", async () => {
  try {
    const response = await fetch("/api/v1/auth/session", {cache: "no-store", credentials: "same-origin"});
    if (response.status === 401) return;
    if (!response.ok) throw new Error("Unable to restore your session. Try signing in again.");
    const data = await response.json(); accessToken = data.access_token;
    await loadSession(); notice("Session restored.");
  } catch (error) { notice(error.message, true); }
});

async function checkIdentityPair(form = $("record-form")) {
  const front = form.elements.identity_front.files[0], back = form.elements.identity_back.files[0];
  const error = $("identity-pair-error"); error.hidden = true; error.textContent = "";
  if (!front || !back) return true;
  if (front.size > 2 * 1024 * 1024 || back.size > 2 * 1024 * 1024) { error.textContent = "Each ID photograph must be at most 2 MiB."; error.hidden = false; return false; }
  const [first, second] = await Promise.all([front.arrayBuffer(), back.arrayBuffer()]);
  if (form.elements.identity_front.files[0] !== front || form.elements.identity_back.files[0] !== back) return true;
  const a = new Uint8Array(first), b = new Uint8Array(second);
  const same = a.length === b.length && a.every((value, index) => value === b[index]);
  if (same) { error.textContent = "Front and back cannot be the same image. Upload a photograph of each side."; error.hidden = false; }
  return !same;
}
for (const side of ["front", "back"]) $("record-form").elements[`identity_${side}`].addEventListener("change", () => run(() => checkIdentityPair()));
$("discard-enrollment-draft").addEventListener("click", () => run(async () => {
  if (revisingLink) throw new Error("A submitted enrollment cannot be deleted here.");
  await window.draftsWorkspace?.clear("enrollment"); clearRevision();
  $("identity-pair-error").hidden = true; notice("Unfinished enrollment details deleted.");
}));

// Reflect native validation rules, including institution-specific requirements.
function markRequiredFields(root = document) {
  const controls = root.matches?.("input, select, textarea") ? [root] : root.querySelectorAll("input, select, textarea");
  for (const control of controls) {
    const label = control.closest("label") || (control.id && document.querySelector(`label[for="${CSS.escape(control.id)}"]`));
    if (!label) continue;
    const required = control.required && !control.disabled && control.type !== "hidden";
    let marker = label.querySelector(".required-marker");
    if (!required) { marker?.remove(); continue; }
    const existingStar = [...label.childNodes].some(child => child.nodeType === Node.TEXT_NODE && child.textContent.includes("*"));
    if (!marker && !existingStar) {
      marker = node("span", " *", "required-marker"); marker.setAttribute("aria-hidden", "true"); marker.title = "Required";
      label.insertBefore(marker, control);
    }
  }
}
markRequiredFields();
new MutationObserver(records => {
  for (const record of records) {
    if (record.type === "attributes") markRequiredFields(record.target);
    else for (const added of record.addedNodes) if (added.nodeType === Node.ELEMENT_NODE) markRequiredFields(added);
  }
}).observe(document.body, {subtree: true, childList: true, attributes: true, attributeFilter: ["required", "disabled"]});

// Activity renews a valid session; background polling alone never keeps it alive.
let sessionRenewalTimer = null, sessionRenewalRequest = null, lastSessionActivity = 0;
function sessionExpiry() {
  try {
    const encoded = accessToken.split(".")[1].replaceAll("-", "+").replaceAll("_", "/");
    const payload = JSON.parse(atob(encoded));
    return {expires: payload.exp * 1000, renewBefore: Math.min(300000, Math.max(15000, (payload.exp - payload.iat) * 200))};
  } catch { return null; }
}
function stopSessionRenewal() { clearInterval(sessionRenewalTimer); sessionRenewalTimer = null; lastSessionActivity = 0; }
function startSessionRenewal() {
  stopSessionRenewal(); lastSessionActivity = Date.now();
  sessionRenewalTimer = setInterval(() => { renewActiveSession().catch(() => {}); }, 10000);
}
async function renewActiveSession() {
  if (!accessToken || !currentUser || document.hidden || Date.now() - lastSessionActivity > 300000) return;
  const expiry = sessionExpiry();
  if (!expiry || expiry.expires - Date.now() > expiry.renewBefore) return;
  if (sessionRenewalRequest) return sessionRenewalRequest;
  const generation = sessionGeneration, token = accessToken;
  const request = (async () => {
    const response = await fetch("/api/v1/auth/session/refresh", {method: "POST", headers: {Authorization: `Bearer ${token}`}, credentials: "same-origin", cache: "no-store"});
    if (generation !== sessionGeneration) return;
    if (response.status === 401) {
      signOutView(); notice("Your session expired or was revoked. Sign in again; your saved checkout details are retained.", true); return;
    }
    if (!response.ok) throw new Error("Session renewal is temporarily unavailable.");
    const data = await response.json();
    if (generation === sessionGeneration) accessToken = data.access_token;
  })();
  sessionRenewalRequest = request;
  try { await request; } finally { if (sessionRenewalRequest === request) sessionRenewalRequest = null; }
}
function recordSessionActivity() {
  if (!currentUser || document.hidden) return;
  lastSessionActivity = Date.now(); renewActiveSession().catch(() => {});
}
for (const event of ["pointerdown", "keydown", "scroll"]) document.addEventListener(event, recordSessionActivity, {passive: true, capture: true});
document.addEventListener("visibilitychange", () => { if (!document.hidden) recordSessionActivity(); });
