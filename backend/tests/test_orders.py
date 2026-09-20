from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.main import app
from app.models.academic import AcademicRecordLink, InstitutionService
from app.models.orders import Order, OrderEvent, OrderItem, OrderQuote
from app.models.user import UserRole
from app.services.order_attachments import MAX_ATTACHMENT_BYTES, get_attachment_scanner
from app.services.orders import CONSENT_VERSION, utcnow
from tests.fixtures_academic import SERVICE, grant
from tests.test_academic_records import create, decision, review_url

BASE = "/api/v1/orders"
RECIPIENT = {
    "key": "recipient1",
    "name": "Admissions",
    "email": "admissions@example.com",
    "delivery_method": "secure_electronic",
}


def checked(response, code=200):
    assert response.status_code == code, response.text
    return response.json()


@pytest.fixture
def workflow(client, catalog, user_factory, auth_headers):
    user = user_factory()
    link = create(client, catalog, user, auth_headers)
    checked(
        client.post(
            review_url(catalog, link["id"]) + "/decisions",
            headers=auth_headers(catalog["staff"]),
            json=decision(),
        )
    )
    headers = {**auth_headers(user), "Idempotency-Key": "create-order-001"}
    order = checked(
        client.post(
            BASE, headers=headers, json={"academic_record_link_id": link["id"]}
        ),
        201,
    )
    body = {
        "expected_version": order["version"],
        "purpose": "Graduate admission",
        "recipients": [RECIPIENT],
        "items": [
            {
                "key": "item1",
                "service_id": catalog["service"].id,
                "recipient_key": "recipient1",
                "quantity": 2,
            }
        ],
    }
    order = checked(client.put(f"{BASE}/{order['id']}", headers=headers, json=body))
    return {
        "user": user,
        "headers": headers,
        "order": order,
        "body": body,
        "link": link,
        "url": f"{BASE}/{order['id']}",
        "staff_url": f"/api/v1/staff/institutions/{catalog['institution'].id}/orders/{order['id']}",
        "staff_headers": auth_headers(catalog["staff"]),
    }


def authorize(client, w):
    order = checked(client.get(w["url"], headers=w["headers"]))
    quote = checked(
        client.post(
            w["url"] + "/quotes",
            headers=w["headers"],
            json={"expected_version": order["version"]},
        ),
        201,
    )
    consent = checked(
        client.post(
            w["url"] + "/consents",
            headers=w["headers"],
            json={
                "quote_id": quote["id"],
                "text_version": CONSENT_VERSION,
                "accepted": True,
            },
        )
    )
    return quote, {
        "expected_version": order["version"],
        "quote_id": quote["id"],
        "consent_id": consent["id"],
    }


def submit(client, w):
    quote, payload = authorize(client, w)
    return (
        checked(client.post(w["url"] + "/submit", headers=w["headers"], json=payload)),
        quote,
        payload,
    )


def test_pricing_consent_immutable_submission_and_retry(client, workflow, db, catalog):
    w = workflow
    result, quote, payload = submit(client, w)
    assert result["status"] == "submitted" and result["payment_status"] == "not_started"
    assert quote["total_minor"] == 300100
    assert result["submitted_snapshot"] == quote["snapshot"]
    assert all(i["fulfillment_status"] == "awaiting_review" for i in result["items"])
    assert "ARCHIVE/001" not in str(result)
    assert (
        checked(client.post(w["url"] + "/submit", headers=w["headers"], json=payload))[
            "version"
        ]
        == result["version"]
    )
    assert (
        client.post(
            w["url"] + "/submit",
            headers={**w["headers"], "Idempotency-Key": "different-submit"},
            json=payload,
        ).status_code
        == 409
    )
    assert (
        client.put(
            w["url"],
            headers=w["headers"],
            json={**w["body"], "expected_version": result["version"]},
        ).status_code
        == 409
    )
    catalog["service"].fee_minor = 500000
    db.commit()
    assert (
        checked(client.get(w["url"], headers=w["headers"]))["submitted_snapshot"][
            "total_minor"
        ]
        == 300100
    )
    assert (
        db.scalar(
            select(func.count())
            .select_from(OrderEvent)
            .where(OrderEvent.kind == "submitted")
        )
        == 1
    )
    consents = checked(client.get(w["url"] + "/consents", headers=w["headers"]))
    assert len(consents) == 1 and consents[0]["text_version"] == CONSENT_VERSION
    assert "user_id" not in consents[0]


def test_create_replay_and_cross_request_key_conflict(
    client, workflow, catalog, auth_headers
):
    w = workflow
    replay = checked(
        client.post(
            BASE,
            headers=w["headers"],
            json={"academic_record_link_id": w["link"]["id"]},
        ),
        201,
    )
    assert replay["id"] == w["order"]["id"]
    link = create(
        client, catalog, w["user"], auth_headers, admission_number="SECOND/001"
    )
    assert (
        client.post(
            BASE, headers=w["headers"], json={"academic_record_link_id": link["id"]}
        ).status_code
        == 409
    )
    assert (
        client.post(
            BASE,
            headers=auth_headers(w["user"]),
            json={"academic_record_link_id": link["id"]},
        ).status_code
        == 422
    )


@pytest.mark.parametrize(
    "change",
    [
        "draft",
        "price",
        "policy",
        "record_version",
        "expired",
        "unmatched",
        "closed",
        "disabled_service",
        "unapproved",
        "missing_field",
    ],
)
def test_changes_invalidate_quote_before_consent_or_submission(
    client, workflow, catalog, db, change
):
    w = workflow
    quote, payload = authorize(client, w)
    expected = 409
    if change == "draft":
        checked(
            client.put(
                w["url"],
                headers=w["headers"],
                json={
                    **w["body"],
                    "expected_version": w["order"]["version"],
                    "purpose": "Changed purpose",
                },
            )
        )
    elif change == "price":
        catalog["service"].fee_minor += 1
    elif change == "policy":
        catalog["policy"].version += 1
    elif change == "record_version":
        db.get(AcademicRecordLink, w["link"]["id"]).version += 1
    elif change == "expired":
        db.get(OrderQuote, quote["id"]).expires_at = utcnow() - timedelta(seconds=1)
    elif change == "unmatched":
        db.get(AcademicRecordLink, w["link"]["id"]).status = "needs_information"
        expected = 422
    elif change == "closed":
        catalog["policy"].accepting_requests = False
        expected = 422
    elif change == "disabled_service":
        catalog["service"].is_active = False
        expected = 422
    elif change == "unapproved":
        catalog["institution"].is_approved = False
        expected = 422
    else:
        db.get(AcademicRecordLink, w["link"]["id"]).program = None
        expected = 422
    db.commit()
    assert (
        client.post(
            w["url"] + "/submit", headers=w["headers"], json=payload
        ).status_code
        == expected
    )
    assert checked(client.get(w["url"], headers=w["headers"]))["status"] == "draft"


def test_consent_is_explicit_and_bound_to_exact_quote(client, workflow):
    w = workflow
    quote, payload = authorize(client, w)
    for change, code in [({"accepted": False}, 422), ({"text_version": "old"}, 409)]:
        assert (
            client.post(
                w["url"] + "/consents",
                headers=w["headers"],
                json={
                    "quote_id": quote["id"],
                    "text_version": CONSENT_VERSION,
                    "accepted": True,
                    **change,
                },
            ).status_code
            == code
        )
    quote2 = checked(
        client.post(
            w["url"] + "/quotes",
            headers=w["headers"],
            json={"expected_version": w["order"]["version"]},
        ),
        201,
    )
    assert (
        client.post(
            w["url"] + "/submit",
            headers=w["headers"],
            json={**payload, "quote_id": quote2["id"]},
        ).status_code
        == 422
    )
    assert (
        client.post(
            w["url"] + "/submit",
            headers=w["headers"],
            json={**payload, "consent_id": 99999},
        ).status_code
        == 422
    )


def test_owner_staff_and_institution_isolation(
    client, workflow, user_factory, auth_headers, institutions, db
):
    w = workflow
    stranger = user_factory("stranger@example.com")
    admin = user_factory("admin@example.com", role=UserRole.ADMIN)
    foreign = user_factory("foreign@example.com")
    grant(db, foreign, institutions[1])
    for user in [stranger, admin, foreign]:
        headers = auth_headers(user)
        for suffix in ["", "/timeline", "/messages", "/consents"]:
            assert client.get(w["url"] + suffix, headers=headers).status_code == 404
        assert client.put(w["url"], headers=headers, json=w["body"]).status_code == 404
        assert client.get(w["staff_url"], headers=headers).status_code == 403
        assert (
            client.post(
                BASE,
                headers={**headers, "Idempotency-Key": "stolen-link-key"},
                json={"academic_record_link_id": w["link"]["id"]},
            ).status_code
            == 404
        )
    assert client.get(w["staff_url"], headers=w["staff_headers"]).status_code == 404
    submit(client, w)
    assert (
        checked(client.get(w["staff_url"], headers=w["staff_headers"]))["status"]
        == "submitted"
    )
    foreign_url = (
        f"/api/v1/staff/institutions/{institutions[1].id}/orders/{w['order']['id']}"
    )
    assert client.get(foreign_url, headers=auth_headers(foreign)).status_code == 404
    assert checked(client.get(BASE, headers=auth_headers(stranger))) == []


def test_draft_components_versions_and_server_fee_control(
    client, workflow, institutions, db
):
    w = workflow
    version = w["order"]["version"]
    assert client.put(w["url"], headers=w["headers"], json=w["body"]).status_code == 409
    for changed in [
        {"fee_minor": 1},
        {"status": "submitted"},
        {"items": [{**w["body"]["items"][0], "quantity": 0}]},
        {"items": [{**w["body"]["items"][0], "unit_fee_minor": 1}]},
        {"recipients": [{**RECIPIENT, "email": None}]},
    ]:
        assert (
            client.put(
                w["url"],
                headers=w["headers"],
                json={**w["body"], "expected_version": version, **changed},
            ).status_code
            == 422
        )
    foreign = InstitutionService(institution_id=institutions[1].id, **SERVICE)
    db.add(foreign)
    db.commit()
    db.refresh(foreign)
    assert (
        client.put(
            w["url"],
            headers=w["headers"],
            json={
                **w["body"],
                "expected_version": version,
                "items": [{**w["body"]["items"][0], "service_id": foreign.id}],
            },
        ).status_code
        == 422
    )
    # Referenced recipients cannot be removed until their document items are removed.
    assert (
        client.delete(
            w["url"] + f"/recipients/recipient1?expected_version={version}",
            headers=w["headers"],
        ).status_code
        == 422
    )
    order = checked(
        client.delete(
            w["url"] + f"/items/item1?expected_version={version}", headers=w["headers"]
        )
    )
    order = checked(
        client.delete(
            w["url"] + f"/recipients/recipient1?expected_version={order['version']}",
            headers=w["headers"],
        )
    )
    order = checked(
        client.post(
            w["url"] + "/recipients",
            headers=w["headers"],
            json={**RECIPIENT, "expected_version": order["version"]},
        )
    )
    order = checked(
        client.post(
            w["url"] + "/items",
            headers=w["headers"],
            json={**w["body"]["items"][0], "expected_version": order["version"]},
        )
    )
    assert order["items"][0]["quantity"] == 2
    assert (
        checked(client.post(w["url"] + "/validation", headers=w["headers"]))[
            "total_minor"
        ]
        == 300100
    )


def test_multi_recipient_postal_and_scheduled_release(client, workflow, catalog, db):
    w = workflow
    catalog["service"].delivery_methods = ["secure_electronic", "post"]
    db.commit()
    postal = {
        "key": "recipient2",
        "name": "Registrar",
        "delivery_method": "post",
        "postal_address": {
            "line1": "1 University Road",
            "city": "Nairobi",
            "postal_code": "00100",
            "country_code": "KE",
        },
    }
    body = {
        **w["body"],
        "expected_version": w["order"]["version"],
        "release_when": "after_graduation",
        "recipients": [RECIPIENT, postal],
        "items": [
            w["body"]["items"][0],
            {
                **w["body"]["items"][0],
                "key": "item2",
                "recipient_key": "recipient2",
                "quantity": 1,
            },
        ],
    }
    order = checked(client.put(w["url"], headers=w["headers"], json=body))
    assert (
        client.post(w["url"] + "/validation", headers=w["headers"]).status_code == 422
    )
    checked(
        client.put(
            w["url"],
            headers=w["headers"],
            json={
                **body,
                "expected_version": order["version"],
                "release_instruction": "December 2026 graduation",
            },
        )
    )
    result, quote, _ = submit(client, w)
    assert quote["total_minor"] == 450150
    assert (
        result["submitted_snapshot"]["release_instruction"]
        == "December 2026 graduation"
    )


def upload(
    client, w, name="note.txt", data=b"Additional ordering instructions", version=None
):
    if version is None:
        version = checked(client.get(w["url"], headers=w["headers"]))["version"]
    return client.post(
        w["url"] + "/attachments",
        headers=w["headers"],
        data={"expected_version": str(version)},
        files={"file": (name, data)},
    )


def test_attachments_private_scoped_and_covered_by_consent(
    client, workflow, user_factory, auth_headers
):
    w = workflow
    _, payload = authorize(client, w)
    order = checked(upload(client, w))
    attachment = order["attachments"][0]
    assert "data" not in attachment and attachment["scan_method"] == "utf8_text"
    download = f"/attachments/{attachment['id']}/download"
    assert (
        client.get(w["staff_url"] + download, headers=w["staff_headers"]).status_code
        == 404
    )
    assert (
        client.get(
            w["url"] + download, headers=auth_headers(user_factory("other@example.com"))
        ).status_code
        == 404
    )
    response = client.get(w["url"] + download, headers=w["headers"])
    assert response.content == b"Additional ordering instructions"
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert (
        client.post(
            w["url"] + "/submit", headers=w["headers"], json=payload
        ).status_code
        == 409
    )
    submitted, quote, _ = submit(client, w)
    assert quote["snapshot"]["attachments"][0]["sha256"] == attachment["sha256"]
    assert (
        client.get(w["staff_url"] + download, headers=w["staff_headers"]).status_code
        == 200
    )
    assert upload(client, w).status_code == 409
    assert (
        client.delete(
            w["url"]
            + f"/attachments/{attachment['id']}?expected_version={submitted['version']}",
            headers=w["headers"],
        ).status_code
        == 409
    )


@pytest.mark.parametrize(
    ("name", "data", "code"),
    [
        ("empty.txt", b"", 413),
        ("large.txt", b"x" * (MAX_ATTACHMENT_BYTES + 1), 413),
        ("huge.txt", b"x" * (MAX_ATTACHMENT_BYTES + 100000), 413),
        ("binary.txt", b"\x00bad", 422),
        ("utf8.txt", b"\xff", 422),
        ("script.html", b"<script>", 422),
        ("fake.pdf", b"not pdf", 422),
        ("unscanned.pdf", b"%PDF-1.4 test", 503),
    ],
)
def test_attachment_validation_and_size_limit(
    client, workflow, name, data, code, monkeypatch
):
    monkeypatch.setattr(settings, "ATTACHMENT_SCANNER", "disabled")
    assert upload(client, workflow, name, data).status_code == code


def test_attachment_count_removal_and_scanner_failures(client, workflow, monkeypatch):
    w = workflow
    for _ in range(5):
        order = checked(upload(client, w))
    assert upload(client, w).status_code == 422
    checked(
        client.delete(
            w["url"]
            + f"/attachments/{order['attachments'][0]['id']}?expected_version={order['version']}",
            headers=w["headers"],
        )
    )
    monkeypatch.setattr(settings, "ATTACHMENT_SCANNER", "clamav")
    monkeypatch.setattr(settings, "CLAMAV_COMMAND", "/does/not/exist/clamscan")
    assert upload(client, w, "scan.pdf", b"%PDF-1.4 test").status_code == 503
    from fastapi import HTTPException

    class Scanner:
        def scan(self, data):
            raise HTTPException(422, "Infected")

    app.dependency_overrides[get_attachment_scanner] = Scanner
    assert upload(client, w, "scan.pdf", b"%PDF-1.4 test").status_code == 422

    class CleanScanner:
        def scan(self, data):
            assert data.startswith(b"%PDF-")

    app.dependency_overrides[get_attachment_scanner] = CleanScanner
    order = checked(upload(client, w, "scan.pdf", b"%PDF-1.4 test"))
    assert order["attachments"][-1]["scan_method"] == "clamav"


def test_questions_replies_and_cancellation_decisions(client, workflow):
    w = workflow
    submit(client, w)
    message = checked(
        client.post(
            w["staff_url"] + "/messages",
            headers=w["staff_headers"],
            json={"body": "Which graduation session?", "requires_response": True},
        ),
        201,
    )
    assert (
        checked(client.get(w["url"], headers=w["headers"]))["unanswered_questions"] == 1
    )
    assert (
        client.post(
            w["url"] + "/messages",
            headers=w["headers"],
            json={"body": "Response", "in_reply_to_id": 9999},
        ).status_code
        == 404
    )
    checked(
        client.post(
            w["url"] + "/messages",
            headers=w["headers"],
            json={"body": "December 2026", "in_reply_to_id": message["id"]},
        ),
        201,
    )
    order = checked(client.get(w["url"], headers=w["headers"]))
    assert order["unanswered_questions"] == 0
    payload = {"expected_version": order["version"], "reason": "Recipient changed"}
    requested = checked(
        client.post(
            w["url"] + "/cancellation-requests", headers=w["headers"], json=payload
        )
    )
    replay = checked(
        client.post(
            w["url"] + "/cancellation-requests", headers=w["headers"], json=payload
        )
    )
    assert (
        replay["version"] == requested["version"] and len(replay["cancellations"]) == 1
    )
    order = checked(
        client.post(
            w["staff_url"] + "/cancellation-decisions",
            headers=w["staff_headers"],
            json={
                "expected_version": requested["version"],
                "decision": "rejected",
                "reason": "Please confirm the new recipient first.",
            },
        )
    )
    assert order["status"] == "submitted"
    requested = checked(
        client.post(
            w["url"] + "/cancellation-requests",
            headers=w["headers"],
            json={**payload, "expected_version": order["version"]},
        )
    )
    order = checked(
        client.post(
            w["staff_url"] + "/cancellation-decisions",
            headers=w["staff_headers"],
            json={
                "expected_version": requested["version"],
                "decision": "approved",
                "reason": "Cancelled before processing.",
            },
        )
    )
    assert order["status"] == "cancelled"
    assert all(i["fulfillment_status"] == "cancelled" for i in order["items"])
    assert len(order["cancellations"]) == 2


def test_cancelled_drafts_never_exposed_to_staff(client, workflow, catalog, db):
    w = workflow
    checked(upload(client, w))
    catalog["institution"].is_approved = False
    db.commit()
    order = checked(client.get(w["url"], headers=w["headers"]))
    order = checked(
        client.post(
            w["url"] + "/cancellation-requests",
            headers=w["headers"],
            json={"expected_version": order["version"], "reason": "No longer needed"},
        )
    )
    assert order["status"] == "cancelled" and order["submitted_at"] is None
    catalog["institution"].is_approved = True
    db.commit()
    assert client.get(w["staff_url"], headers=w["staff_headers"]).status_code == 404
    assert (
        checked(
            client.get(w["staff_url"].rsplit("/", 1)[0], headers=w["staff_headers"])
        )
        == []
    )


@pytest.mark.parametrize("progress", ["payment", "fulfillment"])
def test_cancellation_rechecks_processing_and_payment(client, workflow, db, progress):
    w = workflow
    order, _, _ = submit(client, w)
    requested = checked(
        client.post(
            w["url"] + "/cancellation-requests",
            headers=w["headers"],
            json={"expected_version": order["version"], "reason": "Changed plans"},
        )
    )
    if progress == "payment":
        db.get(Order, order["id"]).payment_status = "paid"
    else:
        db.scalar(
            select(OrderItem).where(OrderItem.order_id == order["id"])
        ).fulfillment_status = "processing"
    db.commit()
    assert (
        client.post(
            w["staff_url"] + "/cancellation-decisions",
            headers=w["staff_headers"],
            json={
                "expected_version": requested["version"],
                "decision": "approved",
                "reason": "Too late",
            },
        ).status_code
        == 409
    )


def test_reorder_copies_choices_without_consent_or_attachments(
    client, workflow, catalog, db
):
    w = workflow
    checked(upload(client, w))
    submit(client, w)
    headers = {**w["headers"], "Idempotency-Key": "repeat-order-001"}
    draft = checked(client.post(w["url"] + "/reorder", headers=headers), 201)
    replay = checked(client.post(w["url"] + "/reorder", headers=headers), 201)
    assert draft["id"] == replay["id"] != w["order"]["id"]
    assert draft["status"] == "draft" and draft["submitted_snapshot"] is None
    assert draft["attachments"] == [] and draft["items"][0]["quantity"] == 2
    url = f"{BASE}/{draft['id']}"
    assert checked(client.get(url + "/consents", headers=w["headers"])) == []
    catalog["service"].fee_minor = 200000
    db.commit()
    quote = checked(
        client.post(
            url + "/quotes",
            headers=w["headers"],
            json={"expected_version": draft["version"]},
        ),
        201,
    )
    assert quote["total_minor"] == 400000


def test_concurrent_submission_and_edits_are_serialized(client, workflow, engine, db):
    if engine.dialect.name != "postgresql":
        pytest.skip("Row locking requires PostgreSQL")
    w = workflow
    _, payload = authorize(client, w)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda _: client.post(
                    w["url"] + "/submit", headers=w["headers"], json=payload
                ),
                range(2),
            )
        )
    assert [r.status_code for r in responses] == [200, 200]
    assert (
        db.scalar(
            select(func.count())
            .select_from(OrderEvent)
            .where(OrderEvent.kind == "submitted")
        )
        == 1
    )
    draft = checked(
        client.post(
            w["url"] + "/reorder",
            headers={**w["headers"], "Idempotency-Key": "concurrent-repeat"},
        ),
        201,
    )
    body = {**w["body"], "expected_version": draft["version"]}
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda _: client.put(
                    f"{BASE}/{draft['id']}", headers=w["headers"], json=body
                ),
                range(2),
            )
        )
    assert sorted(r.status_code for r in responses) == [200, 409]
