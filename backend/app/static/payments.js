"use strict";
const P_keys = new Map();
function P_attempt(container, payment) {
  const card = node("div", undefined, "card");
  card.append(node("strong", `${payment.provider === "stripe" ? "Card" : "M-Pesa"} · ${O_money(payment.amount_minor)}`), node("p", `${payment.mode === "test" ? "TEST — no real funds · " : ""}${payment.status.replaceAll("_", " ")}`));
  if (payment.phone_hint) card.append(node("p", `Mobile ending ${payment.phone_hint}`));
  if (payment.refunded_minor) card.append(node("p", `Refunded: ${O_money(payment.refunded_minor)}`));
  if (payment.refund) card.append(node("p", `Refund ${payment.refund.status}: ${payment.refund.reason}`));
  if (payment.refund?.status === "unknown") card.append(node("p", "The refund outcome is uncertain. Contact the institution for provider investigation before attempting another refund."));
  if (payment.status === "unknown") card.append(node("p", "The provider response is uncertain. Do not start another payment; check status or contact the institution."));
  container.append(card); return card;
}
async function P_student(order) {
  const container = $("student-payments"); container.replaceChildren();
  if (!order.submitted_at) return;
  const data = await api(`/orders/${order.id}/payments`);
  if (O_current !== order) return;
  container.append(node("h3", "Payment"));
  if (!data.available_methods.length && order.payment_status === "not_started") container.append(node("p", "Payments are not enabled for this institution or total yet."));
  if (order.payment_status === "not_started") for (const blocker of data.blockers) container.append(node("p", blocker, "muted"));
  if (data.available_methods.length && !data.blockers.length) F_form(container, "Pay approved order", (form) => {
    if (data.mode === "test") form.append(node("p", "Test checkout: no real money is collected."));
    const provider = O_select(form, "Payment method", "provider", data.available_methods.map((method) => [method, method === "stripe" ? "Card (Stripe checkout)" : "M-Pesa"]));
    const phone = O_input(form, "M-Pesa mobile number", "phone", "", "tel"); phone.placeholder = "0712345678";
    const toggle = () => {phone.parentElement.hidden = provider.value !== "mpesa"; phone.required = provider.value === "mpesa";};
    provider.addEventListener("change", toggle); toggle();
  }, "Start payment", async (form) => {
    if (O_current !== order) throw new Error("Reopen this order before paying.");
    const provider = form.elements.provider.value, phone = provider === "mpesa" ? form.elements.phone.value.trim() : null;
    const signature = `${order.id}:${provider}:${phone || ""}`;
    if (!P_keys.has(signature)) P_keys.set(signature, O_key());
    await api(`/orders/${order.id}/payments`, "POST", {expected_version: data.version, provider, phone}, {"Idempotency-Key": P_keys.get(signature)});
    P_keys.delete(signature); await O_open(order.id); await O_list(); notice("Payment request saved. Complete checkout, then check payment status.");
  });
  for (const payment of data.attempts) {
    const card = P_attempt(container, payment), base = `/orders/${order.id}/payments/${payment.id}`;
    if (payment.checkout_url) {
      const link = node("a", "Open secure card checkout"); link.href = payment.checkout_url; link.target = "_blank"; link.rel = "noopener noreferrer"; card.append(link);
    }
    card.append(action("Check payment status", async () => {await api(base + '/reconcile', 'POST'); await O_open(order.id); await O_list(); notice("Payment status checked with the provider.");}));
    if (payment.provider === "stripe" && payment.status === "unknown") card.append(action("Recover card checkout", async () => {await api(base + '/retry', 'POST'); await O_open(order.id);}));
    if (payment.paid_at) card.append(action("View payment receipt", async () => {
      const receipt = await api(base + '/receipt');
      if (O_current !== order) return;
      const details = node("div", undefined, "history");
      details.append(node("strong", `${receipt.mode === "test" ? "TEST " : ""}Payment receipt`), node("p", receipt.receipt_reference), node("p", `${receipt.order_reference} · ${O_money(receipt.amount_minor)} · ${receipt.status.replaceAll("_", " ")}`), node("p", `Refunded: ${O_money(receipt.refunded_minor)}`), node("p", receipt.notice)); card.append(details);
    }));
    if (payment.status === "succeeded" && !payment.refund) F_form(card, "Request a full refund", (form) => {O_input(form, "Refund reason", "reason").required = true;}, "Request refund", async (form) => {
      await api(base + '/refund-requests', 'POST', {expected_version: data.version, reason: form.elements.reason.value}); await O_open(order.id); notice("Refund request sent for institutional review.");
    });
  }
}
async function P_staff(context, order) {
  const data = await api(`/staff/institutions/${context.id}/orders/${order.id}/payments`);
  if (O_staff !== order || staffContext !== context) return;
  const container = $("staff-payments"); container.replaceChildren(node("h3", "Payment and reconciliation"));
  if (!data.attempts.length) container.append(node("p", "No payment requests yet."));
  for (const payment of data.attempts) {
    const card = P_attempt(container, payment), base = `/staff/institutions/${context.id}/orders/${order.id}/payments/${payment.id}`;
    card.append(action("Reconcile payment", async () => {await api(base + '/reconcile', 'POST'); await O_openStaff(context, order.id); notice("Payment reconciled with the provider.");}));
    if (context.manager && payment.status === "succeeded" && (!payment.refund || (["requested", "rejected"].includes(payment.refund.status) || (payment.provider === "stripe" && payment.refund.status === "unknown")))) F_form(card, "Full refund decision", (form) => {
      const choices = [["refunds", "Approve full refund and cancel order"]];
      if (payment.refund?.status === "requested") choices.push(["refund-rejections", "Reject refund request"]);
      O_select(form, "Decision", "decision", choices);
      O_input(form, "Reason visible to the student", "reason", payment.refund?.reason || "").required = true;
    }, "Save refund decision", async (form) => {
      if (O_staff !== order || staffContext !== context) throw new Error("Reopen this order before saving.");
      await api(base + '/' + form.elements.decision.value, 'POST', {expected_version: data.version, reason: form.elements.reason.value});
      await O_openStaff(context, order.id); notice("Refund decision recorded. Check the provider-confirmed status.");
    });
  }
  const history = document.createElement("details"); history.append(node("summary", "Payment ledger and history"));
  for (const entry of data.ledger) history.append(node("p", `${entry.entry_key}: ${O_money(entry.amount_minor)} · ${new Date(entry.created_at).toLocaleString()}`));
  for (const entry of data.events) history.append(node("p", `${new Date(entry.created_at).toLocaleString()} · ${entry.message}`));
  container.append(history);
}
