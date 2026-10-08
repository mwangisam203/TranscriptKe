"""ID lookup remains an independently reviewed, institution-scoped workflow."""

import json

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.academic import AcademicRecordLink, RecordMatchEvent
from app.models.access import AccessEvent
from tests.fixtures_academic import grant
from tests.test_academic_records import ME, create, decision, review_url, submission

RAW_ID = "12345678"


def test_id_only_submission_is_encrypted_masked_and_reviewed(
    client, catalog, user_factory, auth_headers, db
):
    user = user_factory()
    link = create(
        client, catalog, user, auth_headers, admission_number=None, id_number=RAW_ID
    )
    assert link["admission_number"] is None
    assert link["identity_masked"] == "••••78"
    assert link["status"] == "pending"
    assert RAW_ID not in json.dumps(link)
    stored = db.get(AcademicRecordLink, link["id"])
    assert stored.identity_ciphertext and RAW_ID not in stored.identity_ciphertext
    assert stored.identity_fingerprint != RAW_ID
    history = client.get(f"{ME}/{link['id']}/events", headers=auth_headers(user))
    assert RAW_ID not in history.text
    assert RAW_ID not in json.dumps(
        db.scalar(select(RecordMatchEvent)).submission_snapshot
    )
    url = review_url(catalog, link["id"])
    listed = client.get(url, headers=auth_headers(catalog["staff"]))
    assert RAW_ID not in listed.text
    identity = client.get(url + "/identity", headers=auth_headers(catalog["staff"]))
    assert identity.status_code == 200 and identity.json() == {"id_number": RAW_ID}
    assert identity.headers["Cache-Control"] == "no-store"
    event = db.scalar(
        select(AccessEvent).where(AccessEvent.action == "record_identity_viewed")
    )
    assert event.subject_id == link["id"] and event.actor_id == catalog["staff"].id
    assert RAW_ID not in json.dumps(event.details)
    failed = client.post(
        url + "/decisions",
        headers=auth_headers(catalog["staff"]),
        json=decision(ownership_confirmed=False),
    )
    assert failed.status_code == 422
    matched = client.post(
        url + "/decisions", headers=auth_headers(catalog["staff"]), json=decision()
    )
    assert matched.status_code == 200 and matched.json()["status"] == "matched"
    # ID-only records can enter the existing order workflow after independent review.
    order = client.post(
        "/api/v1/orders",
        headers={**auth_headers(user), "Idempotency-Key": "id-lookup-order"},
        json={"academic_record_link_id": link["id"]},
    )
    assert order.status_code == 201
    assert RAW_ID not in order.text


def test_identity_access_is_institution_scoped(
    client, catalog, user_factory, auth_headers, db, institutions
):
    user = user_factory()
    other = user_factory("other@example.com")
    link = create(
        client, catalog, user, auth_headers, admission_number=None, id_number=RAW_ID
    )
    url = review_url(catalog, link["id"]) + "/identity"
    assert client.get(url).status_code == 401
    foreign_staff = user_factory("foreign-staff@example.com")
    grant(db, foreign_staff, institutions[1])
    for outsider in [user, other, foreign_staff]:
        assert client.get(url, headers=auth_headers(outsider)).status_code == 403
    assert (
        client.get(f"{ME}/{link['id']}", headers=auth_headers(other)).status_code == 404
    )


def test_duplicate_id_and_resubmission_do_not_leak_identifier(
    client, catalog, user_factory, auth_headers, db
):
    user = user_factory()
    link = create(
        client, catalog, user, auth_headers, admission_number=None, id_number=RAW_ID
    )
    duplicate = client.post(
        ME,
        headers=auth_headers(user),
        json=submission(catalog, admission_number=None, id_number=RAW_ID.lower()),
    )
    assert duplicate.status_code == 409 and RAW_ID not in duplicate.text
    url = review_url(catalog, link["id"])
    request = client.post(
        url + "/decisions",
        headers=auth_headers(catalog["staff"]),
        json=decision(outcome="needs_information"),
    )
    assert request.status_code == 200
    payload = submission(
        catalog, admission_number=None, id_number="99887766", expected_version=2
    )
    del payload["institution_id"]
    response = client.post(
        f"{ME}/{link['id']}/resubmissions", headers=auth_headers(user), json=payload
    )
    assert (
        response.status_code == 200 and response.json()["identity_masked"] == "••••66"
    )
    history = client.get(f"{ME}/{link['id']}/events", headers=auth_headers(user))
    assert RAW_ID not in history.text and "CD99887766" not in history.text
    assert db.get(AcademicRecordLink, link["id"]).identity_masked == "••••66"


@pytest.mark.parametrize(
    "changes",
    [
        {"admission_number": None, "id_number": None},
        {"admission_number": None, "id_number": "a"},
        {"admission_number": None, "id_number": "A" * 33},
        {"admission_number": None, "id_number": "<invalid-ID>"},
        {"admission_number": None, "id_number": RAW_ID, "attendance_end_year": 1901},
    ],
)
def test_invalid_inputs_do_not_echo_identity_numbers(
    client, catalog, user_factory, auth_headers, changes
):
    response = client.post(
        ME, headers=auth_headers(user_factory()), json=submission(catalog, **changes)
    )
    assert response.status_code == 422
    assert RAW_ID not in response.text and "input" not in response.json()["detail"][0]


def test_id_lookup_requires_its_own_key_but_admission_lookup_still_works(
    client, catalog, user_factory, auth_headers, monkeypatch
):
    monkeypatch.setattr(settings, "IDENTITY_ENCRYPTION_KEY", None)
    user = user_factory()
    response = client.post(
        ME,
        headers=auth_headers(user),
        json=submission(catalog, admission_number=None, id_number=RAW_ID),
    )
    assert response.status_code == 503
    assert create(client, catalog, user, auth_headers)["admission_number"] == "ADM/001"


def test_downgrade_preserves_id_only_records_by_refusing_data_loss(
    client, catalog, user_factory, auth_headers, db, engine
):
    from alembic import command
    from tests.conftest import migration_config

    user = user_factory()
    link = create(
        client, catalog, user, auth_headers, admission_number=None, id_number=RAW_ID
    )
    with engine.begin() as connection:
        with pytest.raises(RuntimeError, match="before downgrading"):
            command.downgrade(migration_config(connection), "0009")
    db.expire_all()
    assert (
        db.scalar(
            select(AcademicRecordLink.identity_masked).where(
                AcademicRecordLink.id == link["id"]
            )
        )
        == "••••78"
    )


def test_identity_migration_preserves_existing_records_and_foreign_keys(
    client, catalog, user_factory, auth_headers, db, engine
):
    from sqlalchemy import text

    from alembic import command
    from tests.conftest import migration_config

    user = user_factory()
    link = create(client, catalog, user, auth_headers)
    with engine.begin() as connection:
        config = migration_config(connection)
        command.downgrade(config, "0009")
        command.upgrade(config, "head")
        if connection.dialect.name == "sqlite":
            assert connection.scalar(text("PRAGMA foreign_keys")) == 1
            assert not connection.execute(text("PRAGMA foreign_key_check")).all()
        command.check(config)
    db.expire_all()
    assert db.get(AcademicRecordLink, link["id"]).admission_number == "ADM/001"
    assert db.scalar(select(RecordMatchEvent)).link_id == link["id"]
