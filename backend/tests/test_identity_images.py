"""Private proof images are atomic, institution-scoped and independently reviewed."""

import json
from io import BytesIO

import pytest
from PIL import Image, PngImagePlugin
from sqlalchemy import func, select

from app.core.config import settings
from app.main import app
from app.models.academic import AcademicRecordLink, RecordIdentityImage
from app.models.access import AccessEvent
from app.services.order_attachments import MAX_ATTACHMENT_BYTES, get_attachment_scanner
from tests.fixtures_academic import grant
from tests.test_academic_records import ME, decision, review_url, submission


def photograph(color="blue"):
    output = BytesIO()
    info = PngImagePlugin.PngInfo()
    info.add_text("private-metadata", "must-be-removed")
    Image.new("RGB", (100, 70), color=color).save(output, "PNG", pnginfo=info)
    return output.getvalue()


def upload(
    client,
    catalog,
    headers,
    *,
    kind="national_id",
    front=None,
    back=None,
    url="/api/v1/me/academic-record-submissions",
    details=None,
):
    payload = details or submission(catalog)
    payload["identity_document_type"] = kind
    return client.post(
        url,
        headers=headers,
        data={"details": json.dumps(payload)},
        files={
            "front": (
                "front.png",
                photograph() if front is None else front,
                "image/png",
            ),
            "back": (
                "back.png",
                photograph("red") if back is None else back,
                "image/png",
            ),
        },
    )


@pytest.mark.parametrize("kind", ["national_id", "driving_licence"])
def test_images_are_encrypted_and_only_metadata_is_returned(
    client, catalog, user_factory, auth_headers, db, kind
):
    user = user_factory()
    response = upload(client, catalog, auth_headers(user), kind=kind)
    assert response.status_code == 201, response.text
    link = response.json()
    assert link["status"] == "pending" and link["identity_document_type"] == kind
    assert {image["side"] for image in link["identity_images"]} == {"front", "back"}
    assert "ciphertext" not in response.text
    stored = db.scalar(
        select(RecordIdentityImage).where(RecordIdentityImage.side == "front")
    )
    assert (
        b"must-be-removed" not in stored.ciphertext
        and not stored.ciphertext.startswith(b"\xff\xd8")
    )
    download = client.get(
        f"{ME}/{link['id']}/identity-images/front", headers=auth_headers(user)
    )
    assert download.status_code == 200
    assert download.headers["Cache-Control"] == "no-store"
    assert download.headers["X-Content-Type-Options"] == "nosniff"
    assert download.headers["Content-Disposition"].startswith("attachment;")
    assert b"must-be-removed" not in download.content
    with Image.open(BytesIO(download.content)) as clean:
        assert clean.format == "JPEG" and clean.size == (100, 70)
    assert (
        client.post(
            review_url(catalog, link["id"]) + "/decisions",
            headers=auth_headers(catalog["staff"]),
            json=decision(ownership_confirmed=False),
        ).status_code
        == 422
    )


def test_image_downloads_are_owner_and_institution_scoped(
    client, catalog, user_factory, auth_headers, db, institutions
):
    owner = user_factory()
    other = user_factory("outsider@example.com")
    foreign_staff = user_factory("foreign-proof@example.com")
    grant(db, foreign_staff, institutions[1])
    link = upload(client, catalog, auth_headers(owner)).json()
    own = f"{ME}/{link['id']}/identity-images/front"
    staff = review_url(catalog, link["id"]) + "/identity-images/front"
    assert client.get(own).status_code == 401
    assert client.get(own, headers=auth_headers(other)).status_code == 404
    for outsider in [owner, other, foreign_staff]:
        assert client.get(staff, headers=auth_headers(outsider)).status_code == 403
    assert client.get(staff, headers=auth_headers(catalog["staff"])).status_code == 200
    event = db.scalar(
        select(AccessEvent).where(
            AccessEvent.action == "record_identity_image_downloaded"
        )
    )
    assert event.actor_id == catalog["staff"].id and event.subject_id == link["id"]
    assert event.details == {"side": "front"}
    assert (
        client.get(
            own.replace("/front", "/invalid"), headers=auth_headers(owner)
        ).status_code
        == 404
    )


def test_required_images_cannot_be_bypassed_using_json(
    client, catalog, user_factory, auth_headers, db
):
    catalog["policy"].required_fields = ["identity_images"]
    db.commit()
    user = user_factory()
    response = client.post(ME, headers=auth_headers(user), json=submission(catalog))
    assert response.status_code == 422 and "identity_images" in response.text
    assert db.scalar(select(func.count()).select_from(AcademicRecordLink)) == 0
    assert upload(client, catalog, auth_headers(user)).status_code == 201


@pytest.mark.parametrize(
    "bad,code",
    [
        (b"not-an-image", 422),
        (b"\x89PNG\r\n\x1a\n", 422),
        (b"x" * (MAX_ATTACHMENT_BYTES + 1), 413),
    ],
)
def test_failed_back_image_leaves_no_partial_record_or_front(
    client, catalog, user_factory, auth_headers, db, bad, code
):
    user = user_factory()
    response = upload(client, catalog, auth_headers(user), back=bad)
    assert response.status_code == code, response.text
    assert db.scalar(select(func.count()).select_from(AcademicRecordLink)) == 0
    assert db.scalar(select(func.count()).select_from(RecordIdentityImage)) == 0


def test_missing_side_and_invalid_details_are_private(
    client, catalog, user_factory, auth_headers, db
):
    user = user_factory()
    response = client.post(
        "/api/v1/me/academic-record-submissions",
        headers=auth_headers(user),
        data={
            "details": json.dumps(
                submission(catalog, identity_document_type="national_id")
            )
        },
        files={"front": ("front.png", photograph(), "image/png")},
    )
    assert response.status_code == 422
    response = upload(
        client,
        catalog,
        auth_headers(user),
        details=submission(catalog, id_number="PRIVATE_RAW_ID_123"),
    )
    assert response.status_code == 422 and "PRIVATE_RAW_ID_123" not in response.text
    assert db.scalar(select(func.count()).select_from(AcademicRecordLink)) == 0


def test_resubmission_keeps_or_replaces_both_images(
    client, catalog, user_factory, auth_headers, db
):
    user = user_factory()
    headers = auth_headers(user)
    link = upload(client, catalog, headers).json()
    staff_url = review_url(catalog, link["id"]) + "/decisions"
    response = client.post(
        staff_url,
        headers=auth_headers(catalog["staff"]),
        json=decision(
            decision="needs_information",
            ownership_confirmed=False,
            record_reference=None,
        ),
    )
    assert response.status_code == 200, response.text
    details = submission(catalog)
    details.pop("institution_id")
    details["expected_version"] = response.json()["version"]
    resubmitted = client.post(
        f"{ME}/{link['id']}/resubmissions", headers=headers, json=details
    )
    assert (
        resubmitted.status_code == 200
        and len(resubmitted.json()["identity_images"]) == 2
    )
    response = client.post(
        staff_url,
        headers=auth_headers(catalog["staff"]),
        json=decision(
            expected_version=resubmitted.json()["version"],
            decision="needs_information",
            ownership_confirmed=False,
            record_reference=None,
        ),
    )
    details["expected_version"] = response.json()["version"]
    replaced = upload(
        client,
        catalog,
        headers,
        kind="driving_licence",
        url=f"{ME}/{link['id']}/resubmissions-with-images",
        details=details,
    )
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["identity_document_type"] == "driving_licence"
    assert db.scalar(select(func.count()).select_from(RecordIdentityImage)) == 2
    stale = upload(
        client,
        catalog,
        headers,
        url=f"{ME}/{link['id']}/resubmissions-with-images",
        details=details,
    )
    assert stale.status_code == 409
    assert (
        db.get(AcademicRecordLink, link["id"]).identity_document_type
        == "driving_licence"
    )


def test_configured_scanner_rejection_leaves_no_images(
    client, catalog, user_factory, auth_headers, db, monkeypatch
):
    from fastapi import HTTPException

    class RejectScanner:
        def scan(self, data):
            raise HTTPException(422, "Rejected")

    monkeypatch.setattr(settings, "ATTACHMENT_SCANNER", "clamav")
    app.dependency_overrides[get_attachment_scanner] = RejectScanner
    try:
        response = upload(client, catalog, auth_headers(user_factory()))
        assert response.status_code == 422
        assert db.scalar(select(func.count()).select_from(RecordIdentityImage)) == 0
    finally:
        app.dependency_overrides.pop(get_attachment_scanner, None)


def test_service_requirement_is_enforced_and_does_not_leak_image_bytes(
    client, catalog, user_factory, auth_headers, db
):
    catalog["service"].required_fields = ["identity_images"]
    db.commit()
    user = user_factory()
    assert (
        client.post(
            ME, headers=auth_headers(user), json=submission(catalog)
        ).status_code
        == 422
    )
    link = upload(client, catalog, auth_headers(user)).json()
    history = client.get(f"{ME}/{link['id']}/events", headers=auth_headers(user)).json()
    assert history[0]["submission_snapshot"]["identity_image_sides"] == [
        "back",
        "front",
    ]
    assert "ciphertext" not in json.dumps(history)
    assert "must-be-removed" not in json.dumps(history)


def test_quotes_reject_new_image_requirements_for_an_old_confirmed_record(
    client, catalog, user_factory, auth_headers, db
):
    from tests.test_academic_records import create

    user = user_factory()
    link = create(client, catalog, user, auth_headers)
    assert (
        client.post(
            review_url(catalog, link["id"]) + "/decisions",
            headers=auth_headers(catalog["staff"]),
            json=decision(),
        ).status_code
        == 200
    )
    response = client.post(
        "/api/v1/orders",
        headers={**auth_headers(user), "Idempotency-Key": "proof-quote-order"},
        json={
            "academic_record_link_id": link["id"],
            "recipient": {
                "key": "self",
                "name": "Jane",
                "destination_type": "self",
                "email": user.email,
                "delivery_method": "secure_electronic",
            },
        },
    )
    order = response.json()
    url = f"/api/v1/orders/{order['id']}"
    saved = client.put(
        url,
        headers=auth_headers(user),
        json={
            "expected_version": order["version"],
            "purpose": "Application",
            "recipients": order["recipients"],
            "items": [
                {
                    key: item[key]
                    for key in ("key", "service_id", "recipient_key", "quantity")
                }
                for item in order["items"]
            ],
        },
    ).json()
    catalog["policy"].required_fields = ["identity_images"]
    db.commit()
    quote = client.post(
        url + "/quotes",
        headers=auth_headers(user),
        json={"expected_version": saved["version"]},
    )
    assert quote.status_code == 422 and "missing information" in quote.text


@pytest.mark.parametrize("renamed_metadata", [False, True])
def test_identical_sides_are_rejected_atomically(
    client, catalog, user_factory, auth_headers, db, renamed_metadata
):
    front = photograph()
    back = front
    if renamed_metadata:
        output = BytesIO()
        Image.new("RGB", (100, 70), color="blue").save(output, "PNG")
        back = output.getvalue()
        assert back != front
    response = upload(
        client, catalog, auth_headers(user_factory()), front=front, back=back
    )
    assert response.status_code == 422
    assert "different photographs" in response.text
    assert db.scalar(select(func.count()).select_from(AcademicRecordLink)) == 0
    assert db.scalar(select(func.count()).select_from(RecordIdentityImage)) == 0
