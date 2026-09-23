"use strict";
function D_status(container, document) {
  const delivery = document.delivery;
  container.append(node("p", `${document.mode === "demo" ? "DEMO · " : ""}${document.status}${delivery ? ` · Downloads requested: ${delivery.download_count} · Access expires ${new Date(delivery.expires_at).toLocaleString()}` : ""}`));
  if (delivery) container.append(node("p", delivery.notified_at ? `Notification accepted for delivery ${new Date(delivery.notified_at).toLocaleString()}` : "Recipient notification is pending."));
  if (document.revocation_reason) container.append(node("p", `Revoked: ${document.revocation_reason}`));
}
async function D_student(order) {
  const result = await api(`/orders/${order.id}/documents`);
  if (O_current !== order) return;
  const container = $("student-documents"); container.replaceChildren(node("h3", "Issued documents and delivery"));
  if (!result.documents.length) container.append(node("p", "No documents have been prepared for delivery yet."));
  for (const document of result.documents) {
    const item = order.submitted_snapshot?.items.find((i) => i.key === document.item_key);
    const recipient = order.submitted_snapshot?.recipients.find((r) => r.key === item?.recipient_key);
    container.append(node("h4", `${item?.name || document.item_key}${recipient ? ` · ${recipient.name}` : ""}`));
    D_status(container, document);
  }
  if (result.documents.length) container.append(node("p", "Documents are sent to your authorized recipients. A download request does not confirm that the recipient read the document.", "muted"));
}
async function D_staff(context, order) {
  const base = `/staff/institutions/${context.id}/orders/${order.id}/documents`;
  const result = await api(base);
  if (O_staff !== order || staffContext !== context) return;
  const container = $("staff-documents"); container.replaceChildren(node("h3", "Document issuance"));
  if (result.mode === "demo") container.append(node("p", "DEMO delivery — use sample PDFs only. These deliveries are not live academic credentials."));
  for (const blocker of result.blockers) container.append(node("p", blocker, "muted"));
  const current = () => {if (O_staff !== order || staffContext !== context) throw new Error("Reopen this order before continuing.");};
  const save = async (path, payload) => {
    current(); await api(base + path, "POST", {expected_version: result.version, ...payload});
    await O_openStaff(context, order.id); notice("Document update saved.");
  };
  const ready = order.items.filter((i) => i.fulfillment_status === "ready" && !result.documents.some((d) => d.item_key === i.key && d.status !== "revoked"));
  if (ready.length) F_form(container, "Upload institution-prepared PDF", (form) => {
    O_select(form, "Document item", "item_key", ready.map((i) => [i.key, i.key]));
    const input = O_input(form, "PDF file (maximum 2 MiB)", "file"); input.type = "file"; input.accept = ".pdf,application/pdf"; input.required = true;
  }, "Upload PDF", async (form) => {
    current(); const data = new FormData(form); data.append("expected_version", result.version);
    await api(base, "POST", data); await O_openStaff(context, order.id); notice("PDF uploaded and scanned.");
  });
  for (const document of result.documents) {
    const card = node("div", undefined, "card"); card.append(node("h4", document.filename)); D_status(card, document);
    card.append(node("p", `SHA-256: ${document.sha256}`, "muted"));
    card.append(action("Download registrar preview", async () => {
      current(); await O_download(`${base}/${document.id}/preview`, `${document.mode === "demo" ? "DEMO-" : ""}${document.filename}`);
      await O_openStaff(context, order.id);
    }));
    if (document.status === "prepared") F_form(card, "Authorize release", (form) => {
      const input = O_input(form, "I verified this PDF and recipient against the authorized request", "attested"); input.type = "checkbox"; input.required = true;
      O_input(form, "Private release evidence", "internal_note").required = true;
    }, "Issue document", (form) => save(`/${document.id}/issue`, {attested: form.elements.attested.checked, internal_note: form.elements.internal_note.value}));
    if (document.status === "issued") card.append(action("Send recipient notification", () => save(`/${document.id}/notify`, {})));
    if (context.manager && document.status !== "revoked") F_form(card, "Revoke document", (form) => {
      form.append(node("p", "Revocation prevents future downloads. Previously downloaded copies cannot be recalled."));
      O_input(form, "Reason visible to the student", "reason").required = true;
    }, "Revoke document", (form) => save(`/${document.id}/revoke`, {reason: form.elements.reason.value}));
    container.append(card);
  }
}
