"""Personal profiles are private, encrypted and do not bypass verification or matching."""

import json
from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.models.access import AccessEvent
from app.models.profile import UserProfile
from app.models.user import User
from tests.conftest import PASSWORD
from tests.fixtures_academic import grant
from tests.test_academic_records import ME, create, review_url, submission

DETAILS = {
    "first_name": "Jane",
    "middle_name": "Dosy",
    "last_name": "Student",
    "date_of_birth": "1998-02-13",
    "highest_education": "bachelors",
    "country": "Kenya",
    "mobile_phone": "+254 712 345 678",
    "address_line1": "12 Demo Road",
    "address_line2": "",
    "city": "Nairobi",
    "state_region": "Nairobi",
    "postal_code": "00100",
}


def test_guided_registration_encrypts_details_and_requires_email_verification(
    client, mailer, db
):
    response = client.post(
        "/api/v1/auth/register",
        json={"email": "guided@example.com", "password": PASSWORD, "profile": DETAILS},
    )
    assert response.status_code == 201, response.text
    assert response.json()["full_name"] == "Jane Dosy Student"
    assert "date_of_birth" not in response.text and "mobile_phone" not in response.text
    stored = db.scalar(select(UserProfile))
    for secret in ["1998-02-13", "12 Demo Road", "+254712345678"]:
        assert secret not in stored.details_ciphertext
    credentials = {"email": "guided@example.com", "password": PASSWORD}
    assert client.post("/api/v1/auth/login", json=credentials).status_code == 403
    assert (
        client.post(
            "/api/v1/auth/email-verifications/confirm",
            json={"token": mailer.token("email_verification")},
        ).status_code
        == 200
    )
    token = client.post("/api/v1/auth/login", json=credentials).json()["access_token"]
    own = client.get("/api/v1/me/profile", headers={"Authorization": f"Bearer {token}"})
    assert own.status_code == 200 and own.json()["date_of_birth"] == "1998-02-13"
    assert own.json()["mobile_phone"] == "+254712345678"
    assert own.headers["Cache-Control"] == "no-store"


def test_profile_is_owner_only_and_edits_detect_stale_versions(
    client, user_factory, auth_headers, db
):
    owner = user_factory()
    other = user_factory("profile-other@example.com")
    assert client.get("/api/v1/me/profile").status_code == 401
    assert client.get("/api/v1/me/profile", headers=auth_headers(owner)).json() is None
    saved = client.put(
        "/api/v1/me/profile",
        headers=auth_headers(owner),
        json={**DETAILS, "expected_version": 0},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 1
    assert client.get("/api/v1/me/profile", headers=auth_headers(other)).json() is None
    assert (
        client.put(
            "/api/v1/me/profile",
            headers=auth_headers(owner),
            json={**DETAILS, "expected_version": 0},
        ).status_code
        == 409
    )
    changed = client.put(
        "/api/v1/me/profile",
        headers=auth_headers(owner),
        json={**DETAILS, "first_name": "Janet", "expected_version": 1},
    )
    assert changed.status_code == 200 and changed.json()["version"] == 2
    assert (
        client.get("/api/v1/auth/me", headers=auth_headers(owner)).json()["full_name"]
        == "Janet Dosy Student"
    )
    assert (
        client.put(
            "/api/v1/me/profile",
            headers=auth_headers(owner),
            json={**DETAILS, "expected_version": 2, "user_id": other.id},
        ).status_code
        == 422
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"first_name": " "},
        {"date_of_birth": "1998-02-30"},
        {"date_of_birth": (date.today() + timedelta(days=2)).isoformat()},
        {"mobile_phone": "0712345678"},
        {"highest_education": "unknown"},
        {"first_name": "a" * 100, "middle_name": "b" * 100, "last_name": "c" * 100},
    ],
)
def test_invalid_personal_details_do_not_create_an_account(client, db, changes):
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": "invalid-profile@example.com",
            "password": PASSWORD,
            "profile": {**DETAILS, **changes},
        },
    )
    assert response.status_code == 422
    assert db.scalar(select(func.count()).select_from(User)) == 0
    assert "12 Demo Road" not in response.text


def test_registration_rolls_back_if_profile_encryption_is_not_configured(
    client, db, monkeypatch
):
    monkeypatch.setattr(settings, "IDENTITY_ENCRYPTION_KEY", None)
    response = client.post(
        "/api/v1/auth/register",
        json={"email": "no-key@example.com", "password": PASSWORD, "profile": DETAILS},
    )
    assert response.status_code == 503
    assert db.scalar(select(func.count()).select_from(User)) == 0


def test_registrar_can_view_name_and_dob_but_not_contact_details(
    client, catalog, user_factory, auth_headers, db, institutions
):
    owner = user_factory()
    client.put(
        "/api/v1/me/profile",
        headers=auth_headers(owner),
        json={**DETAILS, "expected_version": 0},
    )
    link = create(client, catalog, owner, auth_headers)
    url = review_url(catalog, link["id"]) + "/personal-details"
    foreign = user_factory("foreign-profile@example.com")
    grant(db, foreign, institutions[1])
    assert client.get(url, headers=auth_headers(owner)).status_code == 403
    assert client.get(url, headers=auth_headers(foreign)).status_code == 403
    response = client.get(url, headers=auth_headers(catalog["staff"]))
    assert response.json() == {
        "name": "Jane Dosy Student",
        "date_of_birth": "1998-02-13",
    }
    assert "mobile_phone" not in response.text and "Demo Road" not in response.text
    event = db.scalar(
        select(AccessEvent).where(
            AccessEvent.action == "record_personal_details_viewed"
        )
    )
    assert event.subject_id == link["id"] and "1998" not in json.dumps(event.details)


@pytest.mark.parametrize(
    "changes",
    [
        {"currently_enrolled": True},
        {
            "currently_enrolled": False,
            "attendance_end_year": None,
            "attendance_end_month": None,
        },
        {"currently_enrolled": "yes"},
    ],
)
def test_enrollment_status_must_match_attendance_dates(
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


def test_current_enrollment_is_saved_without_end_date(
    client, catalog, user_factory, auth_headers
):
    link = create(
        client,
        catalog,
        user_factory(),
        auth_headers,
        currently_enrolled=True,
        attendance_end_year=None,
        attendance_end_month=None,
    )
    assert link["currently_enrolled"] is True and link["attendance_end_month"] is None
