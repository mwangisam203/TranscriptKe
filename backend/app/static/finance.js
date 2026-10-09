"use strict";
(() => {
  let billing = null, generation = 0, schoolGeneration = 0, offset = 0;
  async function school(id) {
    const sequence = ++schoolGeneration;
    billing = null; $("school-billing").hidden = true;
    const data = await api(`/admin/institutions/${id}/billing`);
    if (sequence !== schoolGeneration || !currentUser) return;
    billing = data; $("school-billing-form").reset();
    $("school-billing-form").elements.enabled.checked = data.enabled;
    $("school-billing-status").textContent = data.enabled ? "Platform checkout enabled." : "Platform checkout disabled for this school.";
    $("school-billing").hidden = false;
  }
  bindForm("school-billing-form", async form => {
    if (!billing) throw new Error("Select a school first.");
    const data = billing;
    await api(`/admin/institutions/${data.institution_id}/billing`, "PUT", {enabled: form.elements.enabled.checked, expected_version: data.version, reason: form.elements.reason.value});
    if (billing === data) await school(data.institution_id);
    notice("School payment settings saved.");
  });
  async function load(append = false) {
    if (currentUser?.role !== "admin") return;
    const sequence = ++generation;
    if (!append) { offset = 0; $("finance-payments").replaceChildren(); }
    const [payments, collections] = await Promise.all([api(`/admin/finance/payments?offset=${offset}&limit=30`), api("/admin/finance/collections")]);
    if (sequence !== generation || currentUser?.role !== "admin") return;
    $("finance-collections").replaceChildren(node("h4", "Collections by school"));
    $("finance-collections").append(node("p", "Collected less refunds. Transfers to schools are not included.", "muted"));
    for (const row of collections) $("finance-collections").append(node("p", `${row.name} · ${row.mode === "test" ? "TEST · " : ""}${row.currency} ${(row.net_minor / 100).toLocaleString(undefined, {minimumFractionDigits:2, maximumFractionDigits:2})}`));
    if (!payments.length && !append) $("finance-payments").append(node("p", "No platform payment attempts yet."));
    for (const payment of payments) {
      const card = P_attempt($("finance-payments"), payment);
      card.prepend(node("strong", `${payment.institution_name} · ${payment.order_reference}`));
      const base = `/admin/finance/orders/${payment.order_id}/payments/${payment.id}`;
      if (payment.can_check_status) P_button(card, "Check provider status", async () => { await api(base + "/reconcile", "POST"); await load(); notice("Provider status checked."); });
      else if (["unknown", "initiating", "pending"].includes(payment.status)) card.append(node("p", "Provider reference missing: confirm the outcome with the provider before allowing another charge."));
      if (payment.status === "unknown" && !payment.can_check_status) F_form(card, "Resolve unconfirmed payment", form => {
        form.append(node("p", "Investigate with the provider first. Save this decision only when the provider confirms no payment was received. Never use it to bypass an unresolved charge."));
        O_input(form, "Provider case reference", "provider_case_reference").required = true;
        const evidence = O_input(form, "Investigation evidence (at least 30 characters)", "evidence"); evidence.required = true; evidence.minLength = 30; evidence.maxLength = 2000;
        const label = node("label", undefined, "check"), confirmed = document.createElement("input"); confirmed.type = "checkbox"; confirmed.name = "confirmed_no_payment"; confirmed.required = true;
        label.append(confirmed, document.createTextNode("I have confirmed with the provider that no payment was received.")); form.append(label);
      }, "Confirm no payment and allow retry", async form => {
        await api(base + "/no-payment-review", "POST", {expected_version:payment.order_version, provider_case_reference:form.elements.provider_case_reference.value, evidence:form.elements.evidence.value, confirmed_no_payment:form.elements.confirmed_no_payment.checked});
        await load(); notice("Investigation recorded. The customer can retry checkout.");
      });
      if (payment.status === "succeeded" && (!payment.refund || ["requested", "rejected"].includes(payment.refund.status) || (payment.provider === "stripe" && payment.refund.status === "unknown"))) {
        F_form(card, "Platform refund decision", form => {
          const choices = [["refunds", "Approve full refund"]];
          if (payment.refund?.status === "requested") choices.push(["refund-rejections", "Decline refund request"]);
          O_select(form, "Decision", "decision", choices);
          O_input(form, "Reason", "reason", payment.refund?.reason || "").required = true;
        }, "Save platform refund decision", async form => {
          await api(`${base}/${form.elements.decision.value}`, "POST", {expected_version:payment.order_version, reason:form.elements.reason.value});
          await load(); notice("Refund decision saved. Check the provider-confirmed outcome.");
        });
      }
    }
    offset += payments.length; $("finance-more").hidden = payments.length < 30;
  }
  $("finance-refresh").addEventListener("click", () => run(() => load()));
  $("finance-more").addEventListener("click", () => run(() => load(true)));
  window.financeWorkspace = {school, load, reset() {
    generation++; schoolGeneration++; billing = null; offset = 0;
    $("school-billing").hidden = true; $("school-billing-form").reset();
    $("school-billing-status").textContent = "";
    $("finance-payments").replaceChildren(); $("finance-collections").replaceChildren(); $("finance-more").hidden = true;
  }};
})();
