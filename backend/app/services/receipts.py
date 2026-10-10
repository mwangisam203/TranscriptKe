"""Build receipts from the accepted order, never from today's catalog prices."""

from html import escape
from io import BytesIO
from pathlib import Path

import reportlab
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
from sqlalchemy import select

from app.models.orders import OrderItem
from app.models.receipts import PaymentReceiptEmail
from app.models.user import User

NEXT_STEPS = (
    "The institution will review your request and may ask for more information. "
    "Once approved and processed, your documents will be sent to the destination "
    "shown below using your selected delivery method. Follow progress and messages in My orders."
)
NOTICE = "Payment acknowledgment, not a tax invoice. Payment does not guarantee document issuance."


def money(amount, currency):
    return f"{currency} {amount // 100:,}.{amount % 100:02d}"


def capture(db, order, payment):
    accepted = order.submitted_snapshot or {}
    payer = db.get(User, order.user_id)
    identifiers = {
        i.key: i.id
        for i in db.scalars(select(OrderItem).where(OrderItem.order_id == order.id))
    }
    items = [
        dict(i, document_id=identifiers.get(i["key"]))
        for i in accepted.get("items", [])
    ]
    return {
        "payment_id": payment.id,
        "receipt_reference": "PAY-" + payment.id,
        "order_reference": order.reference,
        "payer_name": payer.full_name,
        "payer_email": payer.email,
        "student_name": accepted.get("academic_record", {}).get(
            "name_on_record", payer.full_name
        ),
        "institution": accepted.get("institution", {}),
        "items": items,
        "recipients": accepted.get("recipients", []),
        "amount_minor": payment.amount_minor,
        "currency": payment.currency,
        "paid_at": payment.paid_at.isoformat(),
        "mode": payment.mode,
        "provider": payment.provider,
        "provider_reference": payment.transaction_reference
        or payment.provider_reference
        or "",
        "collected_by": "TranscriptsKE"
        if payment.merchant_scope == "platform"
        else accepted.get("institution", {}).get("name", "Institution"),
    }


def enqueue(db, order, payment):
    if db.get(PaymentReceiptEmail, payment.id) is not None:
        return
    payer = db.get(User, order.user_id)
    if not payer.is_email_verified:
        return
    db.add(
        PaymentReceiptEmail(
            payment_id=payment.id,
            order_id=order.id,
            recipient_email=payer.email,
            snapshot=capture(db, order, payment),
        )
    )


def receipt_snapshot(db, order, payment):
    stored = db.get(PaymentReceiptEmail, payment.id)
    return stored.snapshot if stored else capture(db, order, payment)


def detail_lines(data):
    yield f"Order: {data['order_reference']}"
    yield f"Receipt: {data['receipt_reference']}"
    yield f"Paid: {money(data['amount_minor'], data['currency'])}"
    yield f"Payment date: {data['paid_at']}"
    yield f"Payment method: {'Card' if data['provider'] == 'stripe' else 'M-Pesa'}"
    yield f"Payment reference: {data['provider_reference']}"
    yield f"Collected by: {data['collected_by']}"
    yield f"Paid by: {data['payer_name']} ({data['payer_email']})"
    yield f"Documents for: {data['student_name']}"
    institution = data["institution"]
    yield f"FROM: {institution.get('name', '')} ({institution.get('code', '')})"
    recipients = {r["key"]: r for r in data["recipients"]}
    for item in data["items"]:
        yield f"Ordered item: {item['name']} | Document type: {item['document_type']}"
        yield f"Ordered document/item ID: {item['document_id']} | Service ID: {item['service_id']}"
        yield f"Quantity: {item['quantity']} | Unit fee: {money(item['unit_fee_minor'], data['currency'])} | Item total: {money(item['line_total_minor'], data['currency'])}"
        recipient = recipients.get(item["recipient_key"], {})
        yield f"TO: {recipient.get('name', '')} | {recipient.get('organization') or 'Personal recipient'}"
        yield f"Destination email: {recipient.get('email') or 'Not supplied'}"
        yield f"Delivery method: {recipient.get('delivery_method', '').replace('_', ' ')}"
        if recipient.get("postal_address"):
            yield "Postal address: " + ", ".join(
                str(v) for v in recipient["postal_address"].values() if v
            )


def render_pdf(data):
    # Fonts ship with ReportLab; no external font download or system dependency.
    font_dir = Path(reportlab.__file__).parent / "fonts"
    for name, filename in (("Receipt", "Vera.ttf"), ("ReceiptBold", "VeraBd.ttf")):
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(font_dir / filename)))
    styles = getSampleStyleSheet()
    styles["Normal"].fontName = "Receipt"
    styles["Normal"].fontSize = 9
    styles["Normal"].leading = 15
    styles["Normal"].alignment = TA_LEFT
    styles["Normal"].splitLongWords = True
    styles["Title"].fontName = "ReceiptBold"
    styles["Title"].fontSize = 21
    styles["Title"].textColor = colors.HexColor("#16324f")
    output = BytesIO()
    document = SimpleDocTemplate(
        output,
        title="TranscriptsKE payment receipt",
        author="TranscriptsKE",
        leftMargin=45,
        rightMargin=45,
    )
    story = [
        Paragraph("TranscriptsKE", styles["Title"]),
        Paragraph("Payment receipt and order confirmation", styles["Normal"]),
        Spacer(1, 14),
    ]
    if data["mode"] == "test":
        story += [
            Paragraph(
                "TEST PAYMENT — No real funds received. Demo documents are not academic credentials.",
                styles["Normal"],
            ),
            Spacer(1, 12),
        ]
    for line in detail_lines(data):
        story += [Paragraph(escape(line), styles["Normal"]), Spacer(1, 4)]
    story += [
        Spacer(1, 14),
        Paragraph(escape(NEXT_STEPS), styles["Normal"]),
        Spacer(1, 12),
        Paragraph(escape(NOTICE), styles["Normal"]),
    ]

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Receipt", 8)
        canvas.drawString(45, 25, f"TranscriptsKE | Page {doc.page}")
        canvas.restoreState()

    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return output.getvalue()
