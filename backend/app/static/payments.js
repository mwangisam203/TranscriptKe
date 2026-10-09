"use strict";
const P_keys = new Map(), P_checks = new Map();
function P_reconcile(base) {
  if (!P_checks.has(base)) {
    const request = api(base + "/reconcile", "POST").finally(() => P_checks.delete(base));
    P_checks.set(base, request);
  }
  return P_checks.get(base);
}

function P_button(container, label, handler) {
  const button = node("button", label, "secondary"), feedback = node("p", "", "payment-feedback");
  button.type = "button"; feedback.hidden = true; feedback.setAttribute("role", "status");
  button.addEventListener("click", async () => {
    if (button.disabled) return;
    button.disabled = true; button.textContent = "Please wait…"; feedback.hidden = false;
    feedback.textContent = "Working…"; feedback.classList.remove("error-text");
    try { await handler(); if (feedback.isConnected) feedback.textContent = "Done."; }
    catch (error) { feedback.textContent = error.message; feedback.classList.add("error-text"); feedback.setAttribute("role", "alert"); }
    finally { button.disabled = false; button.textContent = label; }
  });
  container.append(button, feedback); return button;
}
function P_setStatusAction(label, handler) {
  const current = $("order-state").querySelector("button");
  if (current) current.replaceWith(action(label, handler));
}
function P_focusRetry() {
  const form = $("student-payments").querySelector("form");
  form?.scrollIntoView({block: "center", behavior: "smooth"}); form?.elements.provider?.focus({preventScroll: true});
}
function P_paymentHelp(order, payment) {
  const dialog = $("account-support-dialog");
  let context = $("payment-support-context");
  if (!context) { context = node("p", undefined, "payment-help-context"); context.id = "payment-support-context"; dialog.querySelector(".support-dialog-heading").after(context); dialog.addEventListener("close", () => context.remove(), {once: true}); }
  context.textContent = `Payment needs investigation: order ${order.reference}, payment ${payment.id}. No confirmation reference was received. TranscriptsKE support must confirm the outcome before another charge. Your order details remain saved.`;
  if (!dialog.open) dialog.showModal();
}

let P_refreshTimer = null, P_refreshGeneration = 0;
function P_stopRefresh() { clearTimeout(P_refreshTimer); P_refreshTimer = null; P_refreshGeneration++; }
function P_focusPayment() {
  const container = $("student-payments");
  if (!container.hasChildNodes()) return;
  container.scrollIntoView({block: "start", behavior: "smooth"});
  const heading = container.querySelector("h3");
  if (heading) { heading.tabIndex = -1; heading.focus({preventScroll: true}); }
}
function P_watchPayment(order, payment, statusLine) {
  const generation = P_refreshGeneration;
  let checks = 0;
  const check = async () => {
    if (generation !== P_refreshGeneration || O_current !== order || $("orders-view").hidden) return;
    if (document.hidden) { P_refreshTimer = setTimeout(check, 15000); return; }
    try {
      const updated = await P_reconcile(`/orders/${order.id}/payments/${payment.id}`);
      if (generation !== P_refreshGeneration || O_current !== order) return;
      if (updated.status !== payment.status) {
        await O_open(order.id); await O_list();
        notice(["failed", "expired"].includes(updated.status) ? "Payment was unsuccessful. Choose your payment method and retry below." : "Payment status updated.");
        return;
      }
      statusLine.textContent = "Waiting for confirmation. Payment status is checked automatically; you can also check it below.";
      if (++checks < 20) P_refreshTimer = setTimeout(check, 15000);
      else statusLine.textContent = "Still awaiting confirmation. Click Check payment status for an update.";
    } catch {
      if (generation === P_refreshGeneration) statusLine.textContent = "Automatic status checking is unavailable. Click Check payment status to try again.";
    }
  };
  P_refreshTimer = setTimeout(check, 1000);
}
let P_embedded = null, P_cardGeneration = 0, P_script = null;
function P_clearCard() { P_cardGeneration++; if (P_embedded) { P_embedded.destroy(); P_embedded = null; } }
function P_loadCardLibrary() {
  if (window.Stripe) return Promise.resolve();
  if (!P_script) P_script = new Promise((resolve, reject) => {
    const script = document.createElement("script"); script.src = "https://js.stripe.com/v3/";
    script.onload = resolve; script.onerror = () => { P_script = null; script.remove(); reject(new Error("Secure card fields could not load. Check your connection and try again.")); };
    document.head.append(script);
  });
  return P_script;
}
async function P_mountCard(container, order, payment) {
  P_clearCard(); container.querySelectorAll(".secure-card-fields").forEach(element => element.remove()); const sequence = P_cardGeneration;
  const host = node("div", undefined, "secure-card-fields"); container.append(host);
  const base = `/orders/${order.id}/payments/${payment.id}`;
  const data = await api(base + "/card-checkout");
  await P_loadCardLibrary();
  if (sequence !== P_cardGeneration || O_current !== order || !host.isConnected) return;
  const checkout = await window.Stripe(data.publishable_key).initEmbeddedCheckout({
    clientSecret: data.client_secret,
    onComplete: () => run(async () => {
      if (sequence !== P_cardGeneration || O_current !== order) return;
      P_stopRefresh(); await P_reconcile(base);
      if (sequence !== P_cardGeneration || O_current !== order) return;
      await O_open(order.id); await O_list(); notice("Card payment checked. Your order status reflects provider confirmation.");
    })
  });
  if (sequence !== P_cardGeneration || O_current !== order || !host.isConnected) { checkout.destroy(); return; }
  P_embedded = checkout; checkout.mount(host);
}
function P_attempt(container, payment) {
  const card = node("div", undefined, "card");
  const merchant = payment.merchant_scope === "platform" ? "TranscriptsKE support" : "the institution";
  if (payment.merchant_scope === "platform") card.append(node("p", "Collected by TranscriptsKE · Refunds handled by TranscriptsKE", "muted"));
  card.append(node("strong", `${payment.provider === "stripe" ? "Card" : "M-Pesa"} · ${O_money(payment.amount_minor)}`), node("p", `${payment.mode === "test" ? "TEST — no real funds · " : ""}${payment.status === "pending" ? "Awaiting provider confirmation" : payment.status === "unknown" ? "Outcome not confirmed" : payment.status.replaceAll("_", " ")}`));
  if (payment.provider === "mpesa" && payment.status === "pending") card.append(node("p", payment.mode === "test"
    ? "Sandbox STK request accepted. Check the Daraja test result, then check payment status. A test request is not proof of payment."
    : "M-Pesa request accepted. Complete the prompt on your phone, then check payment status. Enter your PIN only in the M-Pesa prompt."));
  if (payment.failure_reason) card.append(node("p", payment.failure_reason, "error-text"));
  if (payment.status === "pending") card.append(node("p", "Payment has not been confirmed. Check the provider status below. If the provider confirms failure or expiry, a retry form appears here; once payment succeeds, your order continues automatically.", "muted"));
  if (payment.phone_hint) card.append(node("p", `Mobile ending ${payment.phone_hint}`));
  if (payment.refunded_minor) card.append(node("p", `Refunded: ${O_money(payment.refunded_minor)}`));
  if (payment.refund) card.append(node("p", `Refund ${payment.refund.status}: ${payment.refund.reason}`));
  if (payment.refund?.status === "unknown") card.append(node("p", `The refund outcome is uncertain. Contact ${merchant} for provider investigation before attempting another refund.`));
  if (payment.status === "unknown") card.append(node("p", `The provider response is uncertain. Do not start another payment; check status or contact ${merchant}.`));
  container.append(card); return card;
}
async function P_student(order) {
  P_clearCard(); P_stopRefresh();
  const container = $("student-payments"); container.replaceChildren();
  if (!order.submitted_at && order.status !== "awaiting_payment") return;
  const data = await api(`/orders/${order.id}/payments`);
  if (O_current !== order) return;
  container.append(node("h3", "Payment"));
  if (data.merchant_scope === "platform") container.append(node("p", "Payments are collected by TranscriptsKE. Refund requests are reviewed by TranscriptsKE.", "muted"));
  if (order.status === "awaiting_payment") container.append(node("p", "Your order is saved privately. The institution receives it only after confirmed payment. Pending or failed payment does not send the order."));
  if (!data.available_methods.length && order.payment_status === "not_started") container.append(node("p", "Payments are not enabled for this institution or total yet."));
  if (!["paid", "refunded"].includes(order.payment_status)) for (const blocker of data.blockers) container.append(node("p", blocker === "An existing payment or refund must be resolved first." ? "Complete the action shown below before starting another payment." : blocker, "muted"));
  const latestAttempt = data.attempts.at(-1);
  const retry = latestAttempt && ["failed", "expired"].includes(latestAttempt.status);
  if (retry && order.payment_status === "not_started") {
    P_setStatusAction(`Payment: ${latestAttempt.status === "expired" ? "expired" : "unsuccessful"} · ${!data.blockers.length && data.available_methods.length ? "Retry payment" : "View payment details"}`, !data.blockers.length && data.available_methods.length ? P_focusRetry : P_focusPayment);
  }
  if (retry && !data.blockers.length) container.append(node("p", "The previous payment was unsuccessful. You can retry this checkout or choose another available payment method."));
  if (data.available_methods.length && !data.blockers.length) F_form(container, retry ? "Retry your payment" : order.collection_policy === "before_review" ? "Pay and send your order" : "Pay approved order", (form) => {
    if (data.mode === "test") form.append(node("p", "Test checkout: no real money is collected."));
    const provider = O_select(form, "Payment method", "provider", data.available_methods.map((method) => [method, method === "stripe" ? "Credit or debit card · Visa / Mastercard" : "M-Pesa"])); provider.required = true;
    if (retry && data.available_methods.includes(latestAttempt.provider)) provider.value = latestAttempt.provider;
    const phone = O_input(form, "M-Pesa mobile number", "phone", "", "tel"); phone.placeholder = "0712345678";
    const cardHelp = node("p", "Visa and Mastercard: enter your card number, expiry date and security code in the secure payment form. Card details are validated there; your bank authorizes payment.", "muted"); form.append(cardHelp);
    const help = node("p", data.mode === "test"
      ? "M-Pesa sandbox: use the test number supplied by your Daraja app. This tests the integration; a real handset prompt is not guaranteed. No real money is collected."
      : "Send a payment prompt to your M-Pesa number. Confirm the amount on your phone and enter your PIN there. We never ask for your PIN here.", "muted");
    form.append(help);
    const toggle = () => {cardHelp.hidden = provider.value !== "stripe"; phone.parentElement.hidden = provider.value !== "mpesa"; phone.required = provider.value === "mpesa"; help.hidden = provider.value !== "mpesa";};
    provider.addEventListener("change", toggle); toggle();
  }, retry ? "Retry payment" : "Start payment", async (form) => {
    if (O_current !== order) throw new Error("Reopen this order before paying.");
    const provider = form.elements.provider.value, phone = provider === "mpesa" ? form.elements.phone.value.trim() : null;
    const signature = `${order.id}:${provider}:${phone || ""}`;
    if (!P_keys.has(signature)) P_keys.set(signature, O_key());
    const payment = await api(`/orders/${order.id}/payments`, "POST", {expected_version: data.version, provider, phone}, {"Idempotency-Key": P_keys.get(signature)});
    P_keys.delete(signature); await O_open(order.id); await O_list();
    notice(payment.status === "failed"
      ? "The payment provider declined this request. You can retry or choose another payment method."
      : payment.status === "unknown"
      ? "The payment request is unconfirmed. Check its status before trying again."
      : provider === "mpesa"
        ? data.mode === "test" ? "Sandbox M-Pesa request saved. Check the Daraja result, then check payment status." : "M-Pesa request saved. Complete the prompt on your phone, then check payment status."
        : "Payment request saved. Complete checkout, then check payment status.");
  });
  const older = data.attempts.length > 1 ? document.createElement("details") : null;
  if (older) older.append(node("summary", "Previous payment attempts"));
  for (const payment of [...data.attempts].reverse()) {
    const target = payment.id === latestAttempt.id ? container : older;
    const card = P_attempt(target, payment), base = `/orders/${order.id}/payments/${payment.id}`;
    if (payment.id === latestAttempt.id && payment.can_check_status && ["pending", "unknown", "initiating"].includes(payment.status)) {
      const statusLine = node("p", "Checking payment status…", "muted"); statusLine.setAttribute("role", "status"); card.append(statusLine);
      P_watchPayment(order, payment, statusLine);
    }
    if (payment.id === latestAttempt.id && ["failed", "expired"].includes(payment.status) && !data.blockers.length && data.available_methods.length) {
      card.append(action("Choose method and retry", P_focusRetry));
    }
    if (payment.embedded_card && payment.status === "pending") {
      card.append(node("h4", "Card details · Visa / Mastercard"));
      card.append(node("p", "If your card is declined, correct the details or try another card in this secure form. Your checkout remains available.", "muted"));
      if (payment.mode === "test") card.append(node("p", "Use sandbox test card numbers here. Real cards are for live checkout only.", "muted"));
      const load = action("Enter card details", async () => { load.disabled = true; try { await P_mountCard(card, order, payment); } finally { load.disabled = false; } }); card.append(load);
    } else if (payment.checkout_url && payment.status === "pending") {
      const link = node("a", "Open secure card checkout"); link.href = payment.checkout_url; link.target = "_blank"; link.rel = "noopener noreferrer"; card.append(link);
    }
    if (payment.can_check_status) P_button(card, "Check payment status", async () => {
      P_stopRefresh(); const updated = await P_reconcile(base);
      if (O_current !== order) return;
      await O_open(order.id); await O_list();
      notice(["failed", "expired"].includes(updated.status) ? "Payment status checked: unsuccessful. Choose your method and retry below." : updated.status === "pending" || updated.status === "unknown" ? "Payment status checked: no final outcome yet. Complete the prompt or check again; retry appears after confirmed failure." : "Payment status checked with the provider.");
    });
    else if (payment.can_resume) P_button(card, payment.provider === "stripe" ? "Recover card checkout" : "Continue payment request", async () => {
      P_stopRefresh(); await api(base + "/retry", "POST");
      if (O_current !== order) return;
      await O_open(order.id); await O_list(); notice("Payment request recovered. Continue below.");
    });
    else if (["unknown", "initiating", "pending"].includes(payment.status)) {
      card.append(node("h4", "Payment needs confirmation"), node("p", "No confirmation reference was received, so this request cannot be checked automatically. Your checkout is saved. Get payment help before retrying to avoid a duplicate charge."));
      P_button(card, "Get payment help", () => P_paymentHelp(order, payment));
      if (payment.id === latestAttempt.id) P_setStatusAction("Payment needs confirmation · Get payment help", () => P_paymentHelp(order, payment));
    }
    if (payment.paid_at) card.append(action("View payment receipt", async () => {
      const receipt = await api(base + '/receipt');
      if (O_current !== order) return;
      const details = node("div", undefined, "history");
      details.append(node("strong", `${receipt.mode === "test" ? "TEST " : ""}Payment receipt`), node("p", receipt.receipt_reference), node("p", `${receipt.order_reference} · ${O_money(receipt.amount_minor)} · ${receipt.status.replaceAll("_", " ")}`), node("p", `Refunded: ${O_money(receipt.refunded_minor)}`), node("p", `Collected by ${receipt.collected_by}`), node("p", receipt.notice)); card.append(details);
    }));
    if (payment.status === "succeeded" && !payment.refund) F_form(card, "Request a full refund", (form) => {O_input(form, "Refund reason", "reason").required = true;}, "Request refund", async (form) => {
      await api(base + '/refund-requests', 'POST', {expected_version: data.version, reason: form.elements.reason.value}); await O_open(order.id); notice(payment.merchant_scope === "platform" ? "Refund request sent to TranscriptsKE for review." : "Refund request sent for institutional review.");
    });
  }
  if (older) container.append(older);
}
async function P_staff(context, order) {
  const data = await api(`/staff/institutions/${context.id}/orders/${order.id}/payments`);
  if (O_staff !== order || staffContext !== context) return;
  const container = $("staff-payments"); container.replaceChildren(node("h3", "Payment and reconciliation"));
  if (!data.attempts.length) container.append(node("p", "No payment requests yet."));
  const older = data.attempts.length > 1 ? document.createElement("details") : null;
  if (older) older.append(node("summary", "Previous payment attempts"));
  for (const payment of [...data.attempts].reverse()) {
    const target = payment.id === data.attempts.at(-1).id ? container : older;
    const card = P_attempt(target, payment), base = `/staff/institutions/${context.id}/orders/${order.id}/payments/${payment.id}`;
    if (payment.can_check_status) P_button(card, "Reconcile payment", async () => {await api(base + "/reconcile", "POST"); await O_openStaff(context, order.id); notice("Payment reconciled with the provider.");});
    else if (["unknown", "initiating", "pending"].includes(payment.status)) card.append(node("p", "No provider reference was received. TranscriptsKE support must investigate this payment."));
    if (context.manager && payment.merchant_scope !== "platform" && payment.status === "succeeded" && (!payment.refund || (["requested", "rejected"].includes(payment.refund.status) || (payment.provider === "stripe" && payment.refund.status === "unknown")))) F_form(card, "Full refund decision", (form) => {
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
  if (older) container.append(older);
  const history = document.createElement("details"); history.append(node("summary", "Payment ledger and history"));
  for (const entry of data.ledger) history.append(node("p", `${entry.entry_key}: ${O_money(entry.amount_minor)} · ${new Date(entry.created_at).toLocaleString()}`));
  for (const entry of data.events) history.append(node("p", `${new Date(entry.created_at).toLocaleString()} · ${entry.message}`));
  container.append(history);
}
