# Shared pytest fixture imports intentionally match test parameter names.
# ruff: noqa: F811
from datetime import timedelta
from email import policy
from email.parser import BytesParser
from io import BytesIO

from pypdf import PdfReader
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app import receipt_worker
from app.core.config import settings
from app.models.academic import InstitutionService
from app.models.orders import Order
from app.models.payments import PaymentAttempt
from app.models.receipts import PaymentReceiptEmail
from app.services.mail import Mailer
from app.services.orders import utcnow
from app.services.receipts import render_pdf
from tests.test_orders import checked, workflow  # noqa: F401
from tests.test_payments import gateway, payable, settle, start  # noqa: F401
from tests.test_upfront_checkout import upfront  # noqa: F401


def confirmed(client, payable, gateway):
    payment = checked(start(client, payable), 201)
    settle(client, payable, gateway, payment)
    return payment


def worker_setup(monkeypatch, engine, mailer):
    monkeypatch.setattr(receipt_worker, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(receipt_worker, "Mailer", lambda: mailer)


def test_confirmed_payment_queues_one_frozen_receipt(client, payable, gateway, db):
    payment = confirmed(client, payable, gateway)
    row = db.get(PaymentReceiptEmail, payment["id"])
    assert row.recipient_email == payable["user"].email
    assert row.recipient_email != row.snapshot["recipients"][0]["email"]
    assert row.snapshot["items"][0]["document_id"] is not None
    assert row.snapshot["student_name"]
    assert row.sent_at is None
    frozen = row.snapshot.copy()
    service = db.get(InstitutionService, row.snapshot["items"][0]["service_id"])
    service.name = "Changed catalog name"
    service.fee_minor = 990000
    db.commit()
    settle(client, payable, gateway, payment)
    assert db.scalar(select(func.count()).select_from(PaymentReceiptEmail)) == 1
    db.refresh(row)
    assert row.snapshot == frozen
    assert "admission_number" not in str(row.snapshot)
    assert "date_of_birth" not in str(row.snapshot)


def test_unpaid_receipt_blocked_and_no_email(client, payable, gateway, db):
    payment = checked(start(client, payable), 201)
    url = payable["pay_url"] + "/" + payment["id"] + "/receipt.pdf"
    assert client.get(url, headers=payable["headers"]).status_code == 409
    gateway.states[payment["id"]] = {"status": "failed", "refunded_minor": 0}
    checked(
        client.post(
            payable["pay_url"] + "/" + payment["id"] + "/reconcile",
            headers=payable["headers"],
        )
    )
    assert db.scalar(select(func.count()).select_from(PaymentReceiptEmail)) == 0


def test_receipt_download_has_full_details_and_is_owner_only(
    client, payable, gateway, user_factory, auth_headers, db
):
    payment = confirmed(client, payable, gateway)
    url = payable["pay_url"] + "/" + payment["id"] + "/receipt.pdf"
    response = client.get(url, headers=payable["headers"])
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["cache-control"] == "no-store"
    text = "\n".join(
        page.extract_text() for page in PdfReader(BytesIO(response.content)).pages
    )
    snapshot = db.get(PaymentReceiptEmail, payment["id"]).snapshot
    for value in (
        "TEST PAYMENT",
        snapshot["order_reference"],
        snapshot["institution"]["name"],
        snapshot["student_name"],
        snapshot["recipients"][0]["email"],
        "Ordered document/item ID:",
        "secure electronic",
        "Once approved and processed",
    ):
        assert value in text
    assert (
        client.get(
            url, headers=auth_headers(user_factory(email="other@example.com"))
        ).status_code
        == 404
    )
    assert client.get(url).status_code == 401


def test_worker_retries_mail_without_changing_confirmed_payment(
    client, payable, gateway, db, engine, mailer, monkeypatch
):
    payment = confirmed(client, payable, gateway)
    worker_setup(monkeypatch, engine, mailer)
    mailer.fail = True
    assert receipt_worker.run() == {"sent": 0, "failed": 1}
    db.expire_all()
    row = db.get(PaymentReceiptEmail, payment["id"])
    assert row.sent_at is None and row.attempt_count == 1
    assert db.get(PaymentAttempt, payment["id"]).status == "succeeded"
    assert db.get(Order, row.order_id).payment_status == "paid"
    assert receipt_worker.run() == {"sent": 0, "failed": 0}
    row.last_attempt_at = utcnow() - timedelta(minutes=2)
    db.commit()
    mailer.fail = False
    assert receipt_worker.run() == {"sent": 1, "failed": 0}
    assert receipt_worker.run() == {"sent": 0, "failed": 0}
    message = next(m for m in mailer.messages if m["purpose"] == "payment_receipt")
    assert message["email"] == payable["user"].email
    assert message["pdf"].startswith(b"%PDF-")
    db.expire_all()
    assert db.get(PaymentReceiptEmail, payment["id"]).sent_at is not None


def test_file_email_attaches_readable_receipt(
    client, payable, gateway, db, tmp_path, monkeypatch
):
    payment = confirmed(client, payable, gateway)
    row = db.get(PaymentReceiptEmail, payment["id"])
    monkeypatch.setattr(settings, "MAIL_DIRECTORY", tmp_path)
    monkeypatch.setattr(settings, "MAIL_BACKEND", "file")
    pdf = render_pdf(row.snapshot)
    Mailer().send_payment_receipt(row.recipient_email, row.snapshot, pdf)
    path = next(tmp_path.glob("*.eml"))
    message = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
    assert message["To"] == row.recipient_email
    assert row.snapshot["order_reference"] in message["Subject"]
    assert (
        row.snapshot["recipients"][0]["email"]
        in message.get_body(preferencelist=("plain",)).get_content()
    )
    attachments = list(message.iter_attachments())
    assert len(attachments) == 1
    assert attachments[0].get_content_type() == "application/pdf"
    assert attachments[0].get_payload(decode=True) == pdf
    assert path.stat().st_mode & 0o777 == 0o600


def test_receipt_wraps_long_names_and_destinations(client, payable, gateway, db):
    payment = confirmed(client, payable, gateway)
    snapshot = db.get(PaymentReceiptEmail, payment["id"]).snapshot
    snapshot["student_name"] = "José & <Student>"
    snapshot["items"] = [
        dict(snapshot["items"][0], name="A very long document title " * 10)
        for _ in range(20)
    ]
    pdf = render_pdf(snapshot)
    pages = PdfReader(BytesIO(pdf)).pages
    assert len(pages) > 1
    text = "\n".join(page.extract_text() for page in pages)
    assert "José & <Student>" in text
    assert "not a tax invoice" in text


def test_rolled_back_confirmation_never_dispatches_email(
    client, payable, gateway, db, engine, mailer, monkeypatch
):
    from app.services.payments import apply_observation

    payment = checked(start(client, payable), 201)
    stored = db.get(PaymentAttempt, payment["id"])
    order = db.get(Order, payable["order"]["id"])
    apply_observation(
        db,
        order,
        stored,
        {
            "status": "succeeded",
            "refunded_minor": 0,
            "transaction_reference": "rollback-test",
        },
    )
    db.flush()
    worker_setup(monkeypatch, engine, mailer)
    # A separate worker can only discover committed rows.
    with sessionmaker(bind=engine)() as reader:
        assert reader.scalar(select(func.count()).select_from(PaymentReceiptEmail)) == 0
    db.rollback()
    assert receipt_worker.run() == {"sent": 0, "failed": 0}
    assert db.get(PaymentAttempt, payment["id"]).status == "pending"
    assert not any(m["purpose"] == "payment_receipt" for m in mailer.messages)


def test_upfront_payment_releases_school_queue_and_queues_confirmation(
    client, upfront, gateway, db
):
    from tests.test_orders import submit

    submit(client, upfront)
    assert db.get(Order, upfront["order"]["id"]).submitted_at is None
    payment = confirmed(client, upfront, gateway)
    db.expire_all()
    order = db.get(Order, upfront["order"]["id"])
    assert order.status == "submitted"
    assert order.submitted_at is not None
    row = db.get(PaymentReceiptEmail, payment["id"])
    assert row.order_id == order.id
    assert row.snapshot["institution"]["id"] == order.institution_id
    assert row.snapshot["order_reference"] == order.reference


def test_rechecking_historical_paid_attempt_does_not_send_old_receipt(
    client, payable, gateway, db
):
    from app.services.payments import apply_observation

    payment = checked(start(client, payable), 201)
    stored = db.get(PaymentAttempt, payment["id"])
    stored.status = "succeeded"
    stored.paid_at = utcnow() - timedelta(days=1)
    stored.transaction_reference = "historical-charge"
    order = db.get(Order, payable["order"]["id"])
    order.payment_status = "paid"
    db.commit()
    apply_observation(
        db,
        order,
        stored,
        {
            "status": "succeeded",
            "refunded_minor": 0,
            "transaction_reference": "historical-charge",
        },
    )
    db.commit()
    assert db.get(PaymentReceiptEmail, stored.id) is None
    assert (
        client.get(
            payable["pay_url"] + "/" + stored.id + "/receipt.pdf",
            headers=payable["headers"],
        ).status_code
        == 200
    )
