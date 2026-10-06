"use strict";
let UI_overviewGeneration = 0;
let UI_pendingView = null;
const UI_views = {
  "student-view": ["MY ACADEMIC RECORDS", "Manage your records and get ready for your next opportunity."],
  "orders-view": ["DOCUMENT REQUESTS", "Create a request, review your quote and follow every step."],
  "account-view": ["YOUR ACCOUNT", "Manage your password and account access."],
  "staff-view": ["INSTITUTION WORKSPACE", "Review academic records and manage your institution’s requests."],
  "admin-view": ["PLATFORM ADMINISTRATION", "Review institution access, onboarding and pilot readiness."]
};
function UI_auth(view, focus = true) {
  if (!["login", "register", "verify", "recover"].includes(view)) return;
  for (const name of ["login", "register", "verify", "recover"]) $(`auth-${name}`).hidden = name !== view;
  $("auth-tabs").hidden = !["login", "register"].includes(view);
  for (const name of ["login", "register"]) {
    const tab = $(`auth-${name}-tab`); tab.setAttribute("aria-selected", String(view === name)); tab.tabIndex = view === name ? 0 : -1;
  }
  if (focus) $(`auth-${view}`).querySelector("input")?.focus({preventScroll: true});
}
for (const button of document.querySelectorAll("[data-auth-view]")) button.addEventListener("click", () => UI_auth(button.dataset.authView));
$("auth-tabs").addEventListener("keydown", (event) => {
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  event.preventDefault();
  const next = event.key === "Home" ? "login" : event.key === "End" ? "register" : event.target.id === "auth-login-tab" ? "register" : "login";
  UI_auth(next, false); $(`auth-${next}-tab`).focus();
});
function UI_profile() {
  const names = currentUser.full_name.trim().split(/\s+/);
  $("account-name").textContent = currentUser.full_name;
  $("settings-name").textContent = currentUser.full_name;
  $("settings-email").textContent = currentUser.email;
  $("account-avatar").textContent = names.slice(0, 2).map((name) => Array.from(name)[0]).join("").toUpperCase();
  $("account-role").textContent = currentUser.role === "admin" ? "Platform administrator" : memberships.some((member) => member.role === "manager") ? "Institution manager" : memberships.length ? "Institution staff" : "Student account";
  document.body.classList.add("is-authenticated");
}
function UI_public(visible) {
  $("public-information").hidden = !visible; $("public-navigation").hidden = !visible;
  $("footer-directory").hidden = !visible;
  document.querySelectorAll(".footer-help-link").forEach((link) => { link.hidden = !visible; });
}
function UI_reset() {
  UI_public(true);
  UI_pendingView = null;
  UI_overviewGeneration++;
  document.body.classList.remove("is-authenticated");
  for (const id of ["account-name", "account-role", "account-avatar", "settings-name", "settings-email"]) $(id).textContent = "";
  for (const id of ["summary-linked", "summary-confirmed", "summary-pending"]) $(id).textContent = "—";
  $("workspace-guide").hidden = true; $("workspace-guide").open = false;
  $("reset-code-details").open = false;
  UI_auth("login", false);
}
async function UI_overview() {
  const generation = ++UI_overviewGeneration, session = sessionGeneration;
  const records = []; let page;
  do {
    page = await api(`/me/academic-record-links?offset=${records.length}&limit=100`);
    if (generation !== UI_overviewGeneration || session !== sessionGeneration || !currentUser) return;
    records.push(...page);
  } while (page.length === 100);
  $("summary-linked").textContent = records.length;
  $("summary-confirmed").textContent = records.filter((record) => record.status === "matched").length;
  $("summary-pending").textContent = records.filter((record) => ["pending", "needs_information"].includes(record.status)).length;
}
function UI_view(id) {
  const [eyebrow, description] = UI_views[id];
  $("view-eyebrow").textContent = eyebrow; $("view-description").textContent = description;
}
function UI_empty(title, description, icon = "record") {
  const container = node("div", undefined, "empty-state"), mark = node("span", undefined, "empty-icon");
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg"); svg.setAttribute("class", "icon"); svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use"); use.setAttribute("href", `#i-${icon}`); svg.append(use); mark.append(svg);
  container.append(mark, node("strong", title), node("p", description)); return container;
}
$("open-workspace-guide").addEventListener("click", () => {$("workspace-guide").hidden = false; $("workspace-guide").open = true; $("workspace-guide").scrollIntoView({block: "nearest"});});
function UI_entered() {
  if (!UI_pendingView) return;
  showView(UI_pendingView); UI_pendingView = null;
  $("workspace").scrollIntoView({block: "start"});
}
for (const button of document.querySelectorAll("[data-workspace-entry]")) button.addEventListener("click", () => {
  const target = button.dataset.workspaceEntry;
  if (!["student-view", "orders-view"].includes(target)) return;
  if (currentUser && !$("workspace").hidden) {
    showView(target); $("workspace").scrollIntoView({block: "start"});
  } else {
    UI_pendingView = target; UI_auth("login");
    notice("Sign in to request documents or check your own order status.");
    $("authentication").scrollIntoView({block: "start"});
  }
});
for (const link of document.querySelectorAll('a[href="/workspace"]')) link.addEventListener("click", (event) => {
  if (!currentUser || $("workspace").hidden || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
  event.preventDefault(); showView("student-view"); $("workspace").scrollIntoView({block: "start"});
});
window.workspaceInterface = {auth: UI_auth, profile: UI_profile, reset: UI_reset, overview: UI_overview, public: UI_public, view: UI_view, empty: UI_empty, entered: UI_entered};
