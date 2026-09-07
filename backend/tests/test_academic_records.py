from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models.academic import (
    AcademicRecordLink,
    InstitutionService,
    OrderingPolicy,
    RecordMatchEvent,
)
from app.models.user import UserRole
from tests.fixtures_academic import SERVICE, grant

ME = "/api/v1/me/academic-record-links"


def submission(catalog, **changes):
    return {
        "institution_id": catalog["institution"].id,
        "service_id": catalog["service"].id,
        "admission_number": " ADM/001 ",
        "name_on_record": "  Jane   Doe ",
        "program": "Computer Science",
        "attendance_start_year": 2018,
        "attendance_end_year": 2022,
        "previous_names": [],
        **changes,
    }


def decision(version=1, outcome="matched", **changes):
    payload = {
        "expected_version": version,
        "decision": outcome,
        "student_message": "Your record ownership has been confirmed.",
    }
    if outcome == "matched":
        payload.update(
            record_reference=" ARCHIVE/001 ",
            ownership_confirmed=True,
            internal_note="Registrar compared the applicant with the institution's independently held enrollment record and confirmed ownership.",
        )
    return {**payload, **changes}


def review_url(catalog, link_id):
    return f"/api/v1/staff/institutions/{catalog['institution'].id}/record-matches/{link_id}"


def create(client, catalog, user, headers, **changes):
    response = client.post(
        ME, headers=headers(user), json=submission(catalog, **changes)
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_student_submits_staff_matches_and_private_evidence_stays_private(
    client, catalog, user_factory, auth_headers, db
):
    student = user_factory()
    link = create(client, catalog, student, auth_headers)
    assert (
        link["status"] == "pending"
        and link["admission_number"] == "ADM/001"
        and link["name_on_record"] == "Jane Doe"
    )
    assert link["requirements_snapshot"]["required_fields"] == [
        "attendance_start_year",
        "program",
    ]
    assert "record_reference" not in link and "user_id" not in link
    url = review_url(catalog, link["id"])
    response = client.post(
        url + "/decisions", headers=auth_headers(catalog["staff"]), json=decision()
    )
    assert response.status_code == 200, response.text
    assert response.json()["record_reference"] == "ARCHIVE/001"
    own = client.get(f"{ME}/{link['id']}", headers=auth_headers(student)).json()
    assert own["status"] == "matched" and own["version"] == 2
    assert "record_reference" not in own
    public_events = client.get(
        f"{ME}/{link['id']}/events", headers=auth_headers(student)
    ).json()
    assert (
        len(public_events) == 2
        and "internal_note" not in public_events[1]
        and "actor_id" not in public_events[1]
    )
    assert "ARCHIVE/001" not in str(public_events)
    private = client.get(url + "/events", headers=auth_headers(catalog["staff"])).json()
    assert private[1]["internal_note"] == decision()["internal_note"]
    assert len(db.scalars(select(RecordMatchEvent)).all()) == 2


@pytest.mark.parametrize(
    "changes",
    [
        {"name_on_record": "   "},
        {"admission_number": "  "},
        {"program": None},
        {"attendance_start_year": None},
        {"attendance_end_year": 1901},
        {"attendance_start_year": datetime.now(timezone.utc).year + 1},
        {"status": "matched"},
        {"record_reference": "anything"},
        {"user_id": 500},
        {"previous_names": ["   "]},
    ],
)
def test_invalid_or_privileged_submissions_rejected(
    client, catalog, user_factory, auth_headers, changes
):
    assert (
        client.post(
            ME,
            headers=auth_headers(user_factory()),
            json=submission(catalog, **changes),
        ).status_code
        == 422
    )


@pytest.mark.parametrize(
    "case",
    [
        "unverified",
        "inactive",
        "unapproved",
        "closed",
        "no_policy",
        "disabled_service",
        "foreign_service",
    ],
)
def test_submission_gates(
    client, catalog, user_factory, auth_headers, institutions, db, case
):
    user = user_factory(verified=case != "unverified")
    data = submission(catalog)
    expected = 409
    if case == "unverified":
        expected = 403
    elif case == "inactive":
        catalog["institution"].is_active = False
        expected = 404
    elif case == "unapproved":
        catalog["institution"].is_approved = False
    elif case == "closed":
        catalog["policy"].accepting_requests = False
    elif case == "no_policy":
        db.delete(catalog["policy"])
    elif case == "disabled_service":
        catalog["service"].is_active = False
        expected = 422
    else:
        other = InstitutionService(institution_id=institutions[1].id, **SERVICE)
        db.add(other)
        db.flush()
        data["service_id"] = other.id
        expected = 422
    db.commit()
    assert (
        client.post(ME, headers=auth_headers(user), json=data).status_code == expected
    )
    assert db.scalar(select(AcademicRecordLink)) is None


def test_previous_names_requirement_accepts_explicit_none(
    client, catalog, user_factory, auth_headers, db
):
    catalog["service"].required_fields = ["previous_names"]
    db.commit()
    user = user_factory()
    payload = submission(catalog)
    del payload["previous_names"]
    assert client.post(ME, headers=auth_headers(user), json=payload).status_code == 422
    assert (
        client.post(
            ME, headers=auth_headers(user), json={**payload, "previous_names": []}
        ).status_code
        == 201
    )


def test_duplicate_claim_and_multiple_institutions(
    client, catalog, user_factory, auth_headers, institutions, db
):
    user = user_factory()
    create(client, catalog, user, auth_headers)
    assert (
        client.post(
            ME,
            headers=auth_headers(user),
            json=submission(catalog, admission_number="adm/001"),
        ).status_code
        == 409
    )
    # Another person's claim is held for review without disclosing the existing claimant.
    create(client, catalog, user_factory("other@example.com"), auth_headers)
    service = InstitutionService(institution_id=institutions[1].id, **SERVICE)
    db.add_all(
        [
            service,
            OrderingPolicy(institution_id=institutions[1].id, accepting_requests=True),
        ]
    )
    db.commit()
    db.refresh(service)
    create(
        client,
        catalog,
        user,
        auth_headers,
        institution_id=institutions[1].id,
        service_id=service.id,
    )
    assert len(client.get(ME, headers=auth_headers(user)).json()) == 2


def test_student_and_staff_isolation_including_platform_admin(
    client, catalog, user_factory, auth_headers, institutions, db
):
    student = user_factory()
    link = create(client, catalog, student, auth_headers)
    other = user_factory("other@example.com")
    admin = user_factory("admin@example.com", role=UserRole.ADMIN)
    foreign_staff = user_factory("foreign@example.com")
    grant(db, foreign_staff, institutions[1])
    for user in [other, admin, foreign_staff]:
        headers = auth_headers(user)
        assert client.get(f"{ME}/{link['id']}", headers=headers).status_code == 404
        assert (
            client.get(f"{ME}/{link['id']}/events", headers=headers).status_code == 404
        )
        assert (
            client.get(review_url(catalog, link["id"]), headers=headers).status_code
            == 403
        )
        assert (
            client.post(
                review_url(catalog, link["id"]) + "/decisions",
                headers=headers,
                json=decision(),
            ).status_code
            == 403
        )
    assert (
        client.get(
            f"/api/v1/staff/institutions/{institutions[1].id}/record-matches/{link['id']}",
            headers=auth_headers(foreign_staff),
        ).status_code
        == 404
    )
    assert client.get(ME, headers=auth_headers(other)).json() == []


@pytest.mark.parametrize(
    "changes",
    [
        {"ownership_confirmed": False},
        {"internal_note": "Only admission ID"},
        {"record_reference": None},
        {"student_message": "  "},
    ],
)
def test_matching_requires_independent_ownership_checks(
    client, catalog, user_factory, auth_headers, changes
):
    link = create(client, catalog, user_factory(), auth_headers)
    assert (
        client.post(
            review_url(catalog, link["id"]) + "/decisions",
            headers=auth_headers(catalog["staff"]),
            json=decision(**changes),
        ).status_code
        == 422
    )


def test_staff_cannot_review_own_record(client, catalog, auth_headers):
    link = create(client, catalog, catalog["manager"], auth_headers)
    assert (
        client.post(
            review_url(catalog, link["id"]) + "/decisions",
            headers=auth_headers(catalog["manager"]),
            json=decision(),
        ).status_code
        == 403
    )


def test_information_request_resubmission_preserves_original_evidence(
    client, catalog, user_factory, auth_headers, db
):
    user = user_factory()
    link = create(client, catalog, user, auth_headers)
    url = review_url(catalog, link["id"]) + "/decisions"
    note = "Please correct the program to match your academic record."
    response = client.post(
        url,
        headers=auth_headers(catalog["staff"]),
        json=decision(outcome="needs_information", student_message=note),
    )
    assert response.status_code == 200 and response.json()["version"] == 2
    data = submission(catalog, program="Applied Computing")
    del data["institution_id"]
    data["expected_version"] = 2
    assert (
        client.post(
            f"{ME}/{link['id']}/resubmissions", headers=auth_headers(user), json=data
        ).status_code
        == 200
    )
    events = client.get(f"{ME}/{link['id']}/events", headers=auth_headers(user)).json()
    assert [event["status"] for event in events] == [
        "pending",
        "needs_information",
        "pending",
    ]
    assert events[0]["submission_snapshot"]["program"] == "Computer Science"
    assert events[2]["submission_snapshot"]["program"] == "Applied Computing"
    assert (
        client.post(
            url, headers=auth_headers(catalog["staff"]), json=decision(version=2)
        ).status_code
        == 409
    )
    assert (
        client.post(
            url, headers=auth_headers(catalog["staff"]), json=decision(version=3)
        ).status_code
        == 200
    )


def test_matched_record_is_immutable_until_manager_reopens(
    client, catalog, user_factory, auth_headers
):
    user = user_factory()
    link = create(client, catalog, user, auth_headers)
    url = review_url(catalog, link["id"]) + "/decisions"
    staff = auth_headers(catalog["staff"])
    assert client.post(url, headers=staff, json=decision()).status_code == 200
    payload = submission(catalog)
    del payload["institution_id"]
    assert (
        client.post(
            f"{ME}/{link['id']}/resubmissions",
            headers=auth_headers(user),
            json={**payload, "expected_version": 2},
        ).status_code
        == 409
    )
    assert (
        client.post(
            url, headers=staff, json=decision(version=2, outcome="needs_information")
        ).status_code
        == 403
    )
    reopened = client.post(
        url,
        headers=auth_headers(catalog["manager"]),
        json=decision(
            version=2,
            outcome="needs_information",
            student_message="We need to review a discrepancy in the original ownership check.",
        ),
    )
    assert reopened.status_code == 200 and reopened.json()["record_reference"] is None
    assert (
        client.post(
            f"{ME}/{link['id']}/resubmissions",
            headers=auth_headers(user),
            json={**payload, "expected_version": 3},
        ).status_code
        == 200
    )


def test_confirmed_record_cannot_belong_to_two_claimants(
    client, catalog, user_factory, auth_headers
):
    first = create(client, catalog, user_factory(), auth_headers)
    second = create(client, catalog, user_factory("other@example.com"), auth_headers)
    staff = auth_headers(catalog["staff"])
    assert (
        client.post(
            review_url(catalog, first["id"]) + "/decisions",
            headers=staff,
            json=decision(),
        ).status_code
        == 200
    )
    response = client.post(
        review_url(catalog, second["id"]) + "/decisions",
        headers=staff,
        json=decision(record_reference="archive/001"),
    )
    assert response.status_code == 409
    assert (
        client.get(review_url(catalog, second["id"]), headers=staff).json()["status"]
        == "pending"
    )


def test_revocation_and_approval_removal_apply_to_existing_requests(
    client, catalog, user_factory, auth_headers, db
):
    student = user_factory()
    link = create(client, catalog, student, auth_headers)
    admin = user_factory("admin@example.com", role=UserRole.ADMIN)
    response = client.put(
        f"/api/v1/admin/institutions/{catalog['institution'].id}/approval",
        headers=auth_headers(admin),
        json={
            "approved": False,
            "expected_approved": True,
            "reason": "Approval suspended for review",
        },
    )
    assert response.status_code == 200
    assert (
        client.post(
            review_url(catalog, link["id"]) + "/decisions",
            headers=auth_headers(catalog["staff"]),
            json=decision(),
        ).status_code
        == 409
    )
    assert (
        client.get(f"{ME}/{link['id']}", headers=auth_headers(student)).status_code
        == 200
    )
    from app.models.access import InstitutionMembership

    member = db.scalar(
        select(InstitutionMembership).where(
            InstitutionMembership.user_id == catalog["staff"].id
        )
    )
    assert (
        client.delete(
            f"/api/v1/staff/institutions/{catalog['institution'].id}/members/{member.id}",
            headers=auth_headers(catalog["manager"]),
        ).status_code
        == 204
    )
    assert (
        client.get(
            review_url(catalog, link["id"]), headers=auth_headers(catalog["staff"])
        ).status_code
        == 403
    )


def test_snapshot_not_changed_by_later_policy_updates(
    client, catalog, user_factory, auth_headers, db
):
    user = user_factory()
    link = create(client, catalog, user, auth_headers)
    catalog["policy"].required_fields = ["previous_names"]
    catalog["policy"].version += 1
    db.commit()
    assert (
        client.get(f"{ME}/{link['id']}", headers=auth_headers(user)).json()[
            "requirements_snapshot"
        ]
        == link["requirements_snapshot"]
    )


def test_status_filter_and_pagination(client, catalog, user_factory, auth_headers):
    user = user_factory()
    first = create(client, catalog, user, auth_headers)
    create(client, catalog, user, auth_headers, admission_number="ADM/002")
    staff = auth_headers(catalog["staff"])
    client.post(
        review_url(catalog, first["id"]) + "/decisions",
        headers=staff,
        json=decision(
            outcome="rejected",
            student_message="No record found with the supplied details. You may correct and resubmit.",
        ),
    )
    base = f"/api/v1/staff/institutions/{catalog['institution'].id}/record-matches"
    assert len(client.get(base + "?status=pending", headers=staff).json()) == 1
    assert len(client.get(base + "?status=rejected", headers=staff).json()) == 1
    assert client.get(base + "?limit=101", headers=staff).status_code == 422
    page = client.get(ME + "?limit=1&offset=1", headers=auth_headers(user)).json()
    assert page[0]["id"] == first["id"]
