"use strict";
const deliveryId = location.hash.slice(1);
history.replaceState(null, "", location.pathname);
const recipientNotice = document.getElementById("recipient-notice");
const validDeliveryId = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(deliveryId);
if (!validDeliveryId) {
  recipientNotice.textContent = "Open the complete delivery link from your notification email.";
  document.querySelectorAll("button").forEach((button) => {button.disabled = true;});
}
for (const [id, path] of [["request-code", "access-codes"], ["download-document", "download"]]) {
  const form = document.getElementById(id);
  form.addEventListener("submit", async (event) => {
    event.preventDefault(); if (!validDeliveryId) return;
    const button = form.querySelector("button"); button.disabled = true;
    try {
      const response = await fetch(`/api/v1/deliveries/${deliveryId}/${path}`, {method: "POST", cache: "no-store", credentials: "omit", headers: {"Content-Type": "application/json"}, body: JSON.stringify(Object.fromEntries(new FormData(form)))});
      if (!response.ok) {const error = await response.json(); throw new Error(typeof error.detail === "string" ? error.detail : "Check your entries and try again.");}
      if (path === "access-codes") recipientNotice.textContent = (await response.json()).message;
      else {
        const blob = await response.blob(); const url = URL.createObjectURL(blob);
        const link = document.createElement("a"); link.href = url;
        link.download = response.headers.get("Content-Disposition")?.match(/filename="([^"]+)"/)?.[1] || "document.pdf";
        link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); form.reset();
        recipientNotice.textContent = "Download requested. For another download, request a new access code.";
      }
    } catch (error) {recipientNotice.textContent = error.message;}
    finally {button.disabled = false;}
  });
}
