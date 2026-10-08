"use strict";
let personalProfile = null, profileGeneration = 0, guidedSignup = false;
const profileFields = [
  ["first_name", "First name *", "text", "given-name", true],
  ["middle_name", "Middle name (optional)", "text", "additional-name", false],
  ["last_name", "Last name *", "text", "family-name", true],
  ["date_of_birth", "Date of birth (month, day and year) *", "date", "bday", true],
  ["highest_education", "Highest level of education *", "select", "", true],
  ["country", "Country *", "text", "country-name", true],
  ["mobile_phone", "Mobile phone with country code *", "tel", "tel", true],
  ["address_line1", "Address line 1 *", "text", "address-line1", true],
  ["address_line2", "Address line 2 (optional)", "text", "address-line2", false],
  ["city", "City / town *", "text", "address-level2", true],
  ["state_region", "County / state / province (optional)", "text", "address-level1", false],
  ["postal_code", "Postal code (optional)", "text", "postal-code", false],
];
const profileEducation = [["", "Choose education level"], ["primary", "Primary"], ["secondary", "Secondary"], ["certificate", "Certificate"], ["diploma", "Diploma / TVET"], ["bachelors", "Bachelor’s degree"], ["masters", "Master’s degree"], ["doctorate", "Doctorate"], ["other", "Other"]];
for (const id of ["registration-profile-fields", "account-profile-fields", "checkout-profile-fields"]) {
  const container = $(id); container.classList.add("profile-fields"); container.append(node("h3", "Personal information"));
  for (const [name, label, type, autocomplete, required] of profileFields) {
    if (name === "country") container.append(node("h3", "Contact information"));
    const wrapper = node("label", label.replace(/ \*$/, "")), control = document.createElement(type === "select" ? "select" : "input");
    if (required) { const marker = node("span", "*", "required-marker"); marker.setAttribute("aria-hidden", "true"); wrapper.append(marker); }
    control.name = name; control.required = required;
    if (type !== "select") { control.type = type; control.autocomplete = autocomplete; control.maxLength = ["first_name", "last_name", "country", "address_line1", "city"].includes(name) ? 100 : 200; }
    if (name === "country") control.value = control.defaultValue = "Kenya";
    if (name === "mobile_phone") { control.placeholder = "+254712345678"; control.maxLength = 32; wrapper.append(node("small", "Include your country code; for example, +254 for Kenya or +1 for the US.", "profile-field-help")); }
    if (name === "date_of_birth") { control.min = "1900-01-01"; control.max = new Date().toISOString().slice(0, 10); }
    if (type === "select") for (const [value, title] of profileEducation) { const option = node("option", title); option.value = value; control.append(option); }
    if (["address_line1", "address_line2"].includes(name)) wrapper.classList.add("profile-field-wide");
    wrapper.append(control); container.append(wrapper);
  }
}
function profileBody(form) {
  return Object.fromEntries(profileFields.map(([name]) => [name, form.elements[name].value.trim()]));
}
function profileName(profile) { return [profile.first_name, profile.middle_name, profile.last_name].filter(Boolean).join(" "); }
function setGuidedSignup(value) {
  guidedSignup = value;
  $("guided-registration-details").hidden = !value; $("guided-registration-details").disabled = !value;
  $("order-signup-guide").hidden = !value; $("cancel-guided-registration").hidden = !value;
  $("basic-registration-name").hidden = value;
  $("register-form").elements.full_name.disabled = value;
  $("registration-confirm-password").hidden = !value;
  $("register-form").elements.confirm_password.disabled = !value;
  $("authentication").classList.toggle("guided-signup", value);
}
function prefillEnrollment() {
  const summary = $("enrollment-personal-summary"); summary.replaceChildren();
  if (!currentUser) { summary.hidden = true; return; }
  summary.hidden = false;
  summary.append(node("strong", `Account name: ${personalProfile ? profileName(personalProfile) : currentUser.full_name}`));
  if (personalProfile) summary.append(node("p", `Date of birth: ${personalProfile.date_of_birth}`));
  else summary.append(node("p", "You can add your birth date and contact information under Account.", "muted"));
  summary.append(node("p", "Confirm your name as it appeared while attending. Enter any former or maiden names separately.", "muted"));
  if (!revisingLink && !$("record-form").elements.name_on_record.value) $("record-form").elements.name_on_record.value = currentUser.full_name;
}
async function loadPersonalProfile() {
  const generation = ++profileGeneration;
  const profile = await api("/me/profile");
  if (generation !== profileGeneration || !currentUser) return;
  personalProfile = profile; setGuidedSignup(false);
  $("personal-profile-form").reset();
  if (profile) fillForm($("personal-profile-form"), profile);
  $("checkout-profile-form").reset();
  $("checkout-personal-details").open = !profile;
  if (profile) fillForm($("checkout-profile-form"), profile);
  $("profile-state").textContent = profile ? "Your details are saved privately. Fields marked * are required." : "No personal profile saved yet. Fields marked * are required.";
  prefillEnrollment();
}
bindForm("personal-profile-form", async (form) => {
  const profile = await api("/me/profile", "PUT", {...profileBody(form), expected_version: personalProfile?.version || 0});
  personalProfile = profile; currentUser.full_name = profileName(profile);
  $("welcome").textContent = `Welcome back, ${profile.first_name}.`;
  window.workspaceInterface.profile(); prefillEnrollment();
  $("profile-state").textContent = "Personal and contact details saved privately.";
  notice("Your personal details have been updated.");
});
bindForm("checkout-profile-form", async form => {
  const profile = await api("/me/profile", "PUT", {...profileBody(form), expected_version: personalProfile?.version || 0});
  personalProfile = profile; currentUser.full_name = profileName(profile);
  fillForm($("personal-profile-form"), profile);
  window.workspaceInterface.profile(); prefillEnrollment();
  await window.draftsWorkspace?.clear("checkout-profile");
  $("checkout-personal-details").open = false;
  $("record-form").scrollIntoView({block: "start"});
  $("student-institution-search").focus({preventScroll: true});
  notice("Continue with your enrollment details, then choose documents and pay.");
});
$("discard-profile-draft").addEventListener("click", () => run(async () => {
  await window.draftsWorkspace?.clear("checkout-profile");
  $("checkout-profile-form").reset();
  if (personalProfile) fillForm($("checkout-profile-form"), personalProfile);
  notice("Unfinished personal details deleted. Your saved account profile is retained.");
}));
$("reload-personal-profile").addEventListener("click", () => run(loadPersonalProfile));
$("cancel-guided-registration").addEventListener("click", () => {
  $("register-form").reset(); setGuidedSignup(false); window.workspaceInterface.auth("login");
});
window.profileWorkspace = {
  guided: setGuidedSignup, load: loadPersonalProfile, prefillEnrollment,
  registrationPayload(form) {
    if (!guidedSignup) return {email: form.elements.email.value, password: form.elements.password.value, full_name: form.elements.full_name.value};
    if (form.elements.password.value !== form.elements.confirm_password.value) throw new Error("The passwords do not match.");
    return {email: form.elements.email.value, password: form.elements.password.value, profile: profileBody(form)};
  },
  staffDetails(container, link, base) {
    const view = action("View account name and birth date", async () => {
      const context = staffContext, selected = reviewingLink;
      const details = await api(`${base}/personal-details`);
      if (staffContext !== context || reviewingLink !== selected) return;
      container.append(node("p", `Account name: ${details.name} · Date of birth: ${details.date_of_birth}`)); view.remove();
    }); container.append(view);
  },
  reset() {
    profileGeneration++; personalProfile = null; setGuidedSignup(false);
    $("checkout-profile-form").reset(); $("checkout-personal-details").open = false;
    $("personal-profile-form").reset(); $("register-form").reset(); $("enrollment-personal-summary").replaceChildren();
    $("enrollment-personal-summary").hidden = true; $("profile-state").textContent = "";
  },
};
