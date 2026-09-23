from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.core.config import settings
from app.main import app
from app.models.issuance import DocumentDelivery, IssuedDocument
from app.models.orders import Order
from app.services.order_attachments import get_attachment_scanner
from app.services.orders import utcnow
from tests.test_fulfillment import action, decision, registrar  # noqa: F401
from tests.test_orders import checked, workflow  # noqa: F401
from tests.test_payments import gateway, settle, start  # noqa: F401

PDF = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n"


class CleanScanner:
    def scan(self, data):
        pass


@pytest.fixture
def ready(client, registrar, gateway, monkeypatch):  # noqa: F811
    w = registrar
    monkeypatch.setattr(settings, "ISSUANCE_ENABLED", True)
    monkeypatch.setattr(settings, "ISSUANCE_MODE", "demo")
    app.dependency_overrides[get_attachment_scanner] = CleanScanner
    checked(decision(client, w))
    checked(decision(client, w, "ready"))
    w["pay_url"] = w["url"] + "/payments"
    w["payment"] = checked(start(client, w), 201)
    settle(client, w, gateway, w["payment"])
    w["docs"] = w["staff_url"] + "/documents"
    yield w
    app.dependency_overrides.pop(get_attachment_scanner, None)


def version(client, w):
    return checked(client.get(w["url"], headers=w["headers"]))["version"]


def upload(client, w, data=PDF, filename="transcript.pdf", headers=None):
    return client.post(
        w["docs"],
        headers=headers or w["staff_headers"],
        data={"expected_version": version(client, w), "item_key": "item1"},
        files={"file": (filename, data, "application/pdf")},
    )


def issue(client, w, doc, **overrides):
    return client.post(
        w["docs"] + f"/{doc['id']}/issue",
        headers=w["staff_headers"],
        json={
            "expected_version": version(client, w),
            "attested": True,
            "internal_note": "PRIVATE: checked original academic PDF and authorized recipient.",
            **overrides,
        },
    )


@pytest.fixture
def issued(client, ready, db):
    w = ready
    doc = checked(upload(client, w), 201)["documents"][0]
    checked(issue(client, w, doc))
    delivery = db.scalar(select(DocumentDelivery))
    w.update(
        doc=doc,
        delivery_id=delivery.id,
        delivery_url=f"/api/v1/deliveries/{delivery.id}",
    )
    return w


def code(client, w, mailer):
    checked(
        client.post(
            w["delivery_url"] + "/access-codes",
            json={"email": "admissions@example.com"},
        ),
        202,
    )
    return mailer.token("document_access")


def test_release_download_and_private_history(client, issued, mailer, db):
    w = issued
    result = checked(
        client.post(
            w["docs"] + f"/{w['doc']['id']}/notify",
            headers=w["staff_headers"],
            json={"expected_version": version(client, w)},
        )
    )
    assert result["documents"][0]["delivery"]["notified_at"]
    assert mailer.messages[-1]["delivery_id"] == w["delivery_id"]
    access = code(client, w, mailer)
    delivery = db.scalar(select(DocumentDelivery))
    assert delivery.code_hash != access and len(delivery.code_hash) == 64
    response = client.post(w["delivery_url"] + "/download", json={"code": access})
    assert response.status_code == 200 and response.content == PDF
    assert response.headers["Cache-Control"] == "no-store"
    assert "DEMO-document" in response.headers["Content-Disposition"]
    assert (
        client.post(w["delivery_url"] + "/download", json={"code": access}).status_code
        == 403
    )
    result = checked(client.get(w["url"] + "/documents", headers=w["headers"]))
    assert result["documents"][0]["delivery"]["download_count"] == 1
    assert all(
        value not in str(result)
        for value in ("PRIVATE", "sha256", "code_hash", "recipient_email")
    )
    assert (
        checked(client.get(w["url"], headers=w["headers"]))["items"][0][
            "fulfillment_status"
        ]
        == "delivered"
    )


@pytest.mark.parametrize(
    "data,filename,expected",
    [
        (b"bad", "bad.pdf", 422),
        (PDF, "bad.txt", 422),
        (b"%PDF-1.4 bad", "bad.pdf", 422),
        (PDF, "../bad.pdf", 422),
        (PDF + b"x" * (2 * 1024 * 1024), "large.pdf", 413),
    ],
)
def test_pdf_validation(client, ready, data, filename, expected):
    assert upload(client, ready, data, filename).status_code == expected


def test_scanner_fails_closed(client, ready):
    app.dependency_overrides.pop(get_attachment_scanner)
    assert upload(client, ready).status_code == 503


def test_malware_rejected(client, ready):
    class Infected:
        def scan(self, data):
            raise HTTPException(422, "Malware found")

    app.dependency_overrides[get_attachment_scanner] = Infected
    assert upload(client, ready).status_code == 422


def test_upload_and_release_permissions(
    client, ready, user_factory, auth_headers, catalog
):
    w = ready
    assert upload(client, w, headers=w["headers"]).status_code == 403
    other = user_factory("other@example.com")
    assert upload(client, w, headers=auth_headers(other)).status_code == 403
    doc = checked(upload(client, w), 201)["documents"][0]
    assert upload(client, w).status_code == 409
    assert issue(client, w, doc, attested=False).status_code == 422
    assert issue(client, w, doc, expected_version=1).status_code == 409
    assert (
        client.get(
            w["docs"] + f"/{doc['id']}/preview", headers=w["headers"]
        ).status_code
        == 403
    )
    checked(issue(client, w, doc))
    assert issue(client, w, doc).status_code == 409
    assert (
        client.get(w["url"] + "/documents", headers=auth_headers(other)).status_code
        == 404
    )


@pytest.mark.parametrize(
    "blocker",
    ["hold", "refund", "record", "consent", "institution", "disabled", "live"],
)
def test_release_gates(client, ready, db, catalog, monkeypatch, blocker):
    from app.models.academic import AcademicRecordLink
    from app.models.orders import OrderConsent

    w = ready
    doc = checked(upload(client, w), 201)["documents"][0]
    order = db.get(Order, w["order"]["id"])
    if blocker == "hold":
        checked(
            action(
                client,
                w,
                "/holds",
                category="academic",
                student_message="Hold",
                internal_note="Review",
            ),
            201,
        )
    elif blocker == "refund":
        order.payment_status = "refund_requested"
    elif blocker == "record":
        db.get(AcademicRecordLink, order.academic_record_link_id).version += 1
    elif blocker == "consent":
        db.get(OrderConsent, order.submission_consent_id).scope_hash = "invalid"
    elif blocker == "institution":
        catalog["institution"].is_approved = False
    elif blocker == "disabled":
        monkeypatch.setattr(settings, "ISSUANCE_ENABLED", False)
    else:
        monkeypatch.setattr(settings, "ISSUANCE_MODE", "live")
    db.commit()
    assert issue(client, w, doc).status_code == 409


def test_recipient_mismatch_rotation_expiry_and_tamper(client, issued, mailer, db):
    w = issued
    before = len(mailer.messages)
    checked(
        client.post(
            w["delivery_url"] + "/access-codes", json={"email": "wrong@example.com"}
        ),
        202,
    )
    assert len(mailer.messages) == before
    old = code(client, w, mailer)
    new = code(client, w, mailer)
    assert (
        client.post(w["delivery_url"] + "/download", json={"code": old}).status_code
        == 403
    )
    doc = db.get(IssuedDocument, w["doc"]["id"])
    doc.data = b"tampered"
    db.commit()
    assert (
        client.post(w["delivery_url"] + "/download", json={"code": new}).status_code
        == 409
    )
    doc.data = PDF
    db.get(DocumentDelivery, w["delivery_id"]).code_expires_at = utcnow() - timedelta(
        seconds=1
    )
    db.commit()
    assert (
        client.post(w["delivery_url"] + "/download", json={"code": new}).status_code
        == 403
    )


def test_revocation_blocks_access_and_refunds_even_after_replacement(
    client, issued, mailer, db, catalog, auth_headers
):
    w = issued
    access = code(client, w, mailer)
    url = w["docs"] + f"/{w['doc']['id']}/revoke"
    payload = {"expected_version": version(client, w), "reason": "Correction required"}
    assert client.post(url, headers=w["staff_headers"], json=payload).status_code == 403
    result = checked(
        client.post(url, headers=auth_headers(catalog["manager"]), json=payload)
    )
    assert result["documents"][0]["status"] == "revoked"
    assert (
        client.post(w["delivery_url"] + "/download", json={"code": access}).status_code
        == 410
    )
    assert issue(client, w, w["doc"]).status_code == 409
    checked(decision(client, w, "ready"))
    replacement = checked(upload(client, w), 201)["documents"][-1]
    assert replacement["id"] != w["doc"]["id"]
    assert (
        client.post(
            w["pay_url"] + f"/{w['payment']['id']}/refund-requests",
            headers=w["headers"],
            json={"expected_version": version(client, w), "reason": "Refund"},
        ).status_code
        == 409
    )
    assert len(list(db.scalars(select(IssuedDocument)))) == 2


@pytest.mark.parametrize("state", ["expiry", "hold", "refund", "suspended"])
def test_download_rechecks_current_gates(client, issued, mailer, db, catalog, state):
    w = issued
    access = code(client, w, mailer)
    if state == "expiry":
        db.get(DocumentDelivery, w["delivery_id"]).expires_at = utcnow() - timedelta(
            seconds=1
        )
    elif state == "hold":
        checked(
            action(
                client,
                w,
                "/holds",
                category="academic",
                student_message="Hold",
                internal_note="Review",
            ),
            201,
        )
    elif state == "refund":
        db.get(Order, w["order"]["id"]).payment_status = "refunded"
    else:
        catalog["institution"].is_active = False
    db.commit()
    assert (
        client.post(w["delivery_url"] + "/download", json={"code": access}).status_code
        == 410
    )


def test_notification_failure_is_retryable(client, issued, mailer, db):
    w = issued
    mailer.fail = True
    url = w["docs"] + f"/{w['doc']['id']}/notify"
    payload = {"expected_version": version(client, w)}
    assert client.post(url, headers=w["staff_headers"], json=payload).status_code == 503
    delivery = db.get(DocumentDelivery, w["delivery_id"])
    assert delivery.notification_attempts == 1 and not delivery.notified_at
    mailer.fail = False
    checked(client.post(url, headers=w["staff_headers"], json=payload))
    db.refresh(delivery)
    assert delivery.notification_attempts == 2 and delivery.notified_at


def test_delivery_codes_are_rate_limited(client, issued):
    statuses = [
        client.post(
            issued["delivery_url"] + "/access-codes",
            json={"email": "wrong@example.com"},
        ).status_code
        for _ in range(6)
    ]
    assert statuses == [202] * 5 + [429]


def test_concurrent_download_consumes_code_once(client, issued, mailer, engine):
    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL row locking")
    w = issued
    access = code(client, w, mailer)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda _: client.post(
                    w["delivery_url"] + "/download", json={"code": access}
                ),
                range(2),
            )
        )
    assert sorted(r.status_code for r in responses) == [200, 403]


def test_notification_worker_sends_once(client, issued, engine, monkeypatch, mailer):
    from sqlalchemy.orm import sessionmaker

    from app import delivery_worker

    monkeypatch.setattr(
        delivery_worker, "SessionLocal", sessionmaker(bind=engine, autoflush=False)
    )
    monkeypatch.setattr(delivery_worker, "Mailer", lambda: mailer)
    assert delivery_worker.run() == {"sent": 1, "failed": 0}
    assert delivery_worker.run() == {"sent": 0, "failed": 0}
    assert (
        len([m for m in mailer.messages if m["purpose"] == "document_notification"])
        == 1
    )


def test_live_configuration_rejects_unsafe_defaults():
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError, match="Live issuance requires"):
        Settings(
            _env_file=None,
            DATABASE_URL="sqlite://",
            SECRET_KEY="a" * 40,
            ISSUANCE_ENABLED=True,
            ISSUANCE_MODE="live",
        )
    with pytest.raises(ValidationError, match="Production cannot issue demo"):
        Settings(
            _env_file=None,
            DATABASE_URL="sqlite://",
            SECRET_KEY="a" * 40,
            ISSUANCE_ENABLED=True,
            APP_ENV="production",
        )


def test_revoked_staff_cannot_preview(client, issued, db, catalog):
    from app.models.access import InstitutionMembership

    member = db.scalar(
        select(InstitutionMembership).where(
            InstitutionMembership.user_id == catalog["staff"].id
        )
    )
    member.is_active = False
    db.commit()
    assert (
        client.get(
            issued["docs"] + f"/{issued['doc']['id']}/preview",
            headers=issued["staff_headers"],
        ).status_code
        == 403
    )


def test_concurrent_issue_creates_one_delivery(client, ready, engine, db):
    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL row locking")
    w = ready
    doc = checked(upload(client, w), 201)["documents"][0]
    expected = version(client, w)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda _: issue(client, w, doc, expected_version=expected), range(2)
            )
        )
    assert sorted(r.status_code for r in responses) == [200, 409]
    assert len(list(db.scalars(select(DocumentDelivery)))) == 1


def test_worker_retries_mail_failure_without_duplicate_release(
    client, issued, engine, monkeypatch, mailer, db
):
    from sqlalchemy.orm import sessionmaker

    from app import delivery_worker

    monkeypatch.setattr(
        delivery_worker, "SessionLocal", sessionmaker(bind=engine, autoflush=False)
    )
    monkeypatch.setattr(delivery_worker, "Mailer", lambda: mailer)
    mailer.fail = True
    assert delivery_worker.run() == {"sent": 0, "failed": 1}
    assert delivery_worker.run() == {"sent": 0, "failed": 0}
    row = db.get(DocumentDelivery, issued["delivery_id"])
    row.notification_attempted_at = utcnow() - timedelta(minutes=6)
    db.commit()
    mailer.fail = False
    assert delivery_worker.run() == {"sent": 1, "failed": 0}
    assert len(list(db.scalars(select(DocumentDelivery)))) == 1


def test_prepared_document_requires_revocation_before_reopening(
    client, ready, catalog, auth_headers
):
    w = ready
    checked(upload(client, w), 201)
    checked(
        client.put(
            w["registrar_url"] + "/assignment",
            headers=auth_headers(catalog["manager"]),
            json={
                "expected_version": version(client, w),
                "user_id": catalog["manager"].id,
            },
        )
    )
    manager = {**w, "staff_headers": auth_headers(catalog["manager"])}
    assert decision(client, manager, "reopen").status_code == 409


def test_issuance_keeps_other_institutions_and_own_staff_out(
    client, ready, catalog, db, institutions, user_factory, auth_headers
):
    from tests.fixtures_academic import grant

    w = ready
    doc = checked(upload(client, w), 201)["documents"][0]
    foreign = user_factory("foreign-registrar@example.com")
    grant(db, foreign, institutions[1])
    assert (
        client.get(
            w["docs"] + f"/{doc['id']}/preview", headers=auth_headers(foreign)
        ).status_code
        == 403
    )
    wrong_scope = w["docs"].replace(
        f"/institutions/{catalog['institution'].id}/",
        f"/institutions/{institutions[1].id}/",
    )
    assert client.get(wrong_scope, headers=auth_headers(foreign)).status_code == 404
    grant(db, w["user"], catalog["institution"])
    assert (
        client.get(
            w["docs"] + f"/{doc['id']}/preview", headers=w["headers"]
        ).status_code
        == 403
    )


def test_document_notification_has_no_pdf_or_access_secret(tmp_path, monkeypatch):
    from app.services.mail import Mailer

    monkeypatch.setattr(settings, "MAIL_DIRECTORY", tmp_path)
    monkeypatch.setattr(settings, "MAIL_BACKEND", "file")
    Mailer().send_document("recipient@example.com", "delivery-identifier", "demo")
    from email import policy
    from email.parser import BytesParser

    message = BytesParser(policy=policy.default).parsebytes(
        next(tmp_path.glob("*.eml")).read_bytes()
    )
    assert "DEMO" in message["Subject"]
    assert "/recipient#delivery-identifier" in message.get_content()
    assert not list(message.iter_attachments())
