"""New attendance dates must include both month and year; history is preserved."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models.academic import AcademicRecordLink, RecordMatchEvent
from tests.test_academic_records import ME, create, decision, review_url, submission


def test_months_persist_in_record_review_and_history(
    client, catalog, user_factory, auth_headers, db
):
    user = user_factory()
    link = create(
        client,
        catalog,
        user,
        auth_headers,
        attendance_start_year=2020,
        attendance_start_month=9,
        attendance_end_year=2022,
        attendance_end_month=6,
    )
    assert link["attendance_start_month"] == 9 and link["attendance_end_month"] == 6
    staff = client.get(
        review_url(catalog, link["id"]), headers=auth_headers(catalog["staff"])
    ).json()
    assert staff["attendance_start_month"] == 9
    event = db.scalar(select(RecordMatchEvent))
    assert event.submission_snapshot["attendance_end_month"] == 6
    response = client.post(
        review_url(catalog, link["id"]) + "/decisions",
        headers=auth_headers(catalog["staff"]),
        json=decision(outcome="needs_information"),
    )
    payload = submission(
        catalog, expected_version=response.json()["version"], attendance_start_month=8
    )
    payload.pop("institution_id")
    revised = client.post(
        f"{ME}/{link['id']}/resubmissions", headers=auth_headers(user), json=payload
    )
    assert revised.status_code == 200 and revised.json()["attendance_start_month"] == 8


@pytest.mark.parametrize(
    "changes",
    [
        {"attendance_start_month": 0},
        {"attendance_end_month": 13},
        {"attendance_start_month": "September"},
        {"attendance_start_month": None},
        {"attendance_start_year": None, "attendance_start_month": 9},
        {
            "attendance_start_year": 2022,
            "attendance_start_month": 9,
            "attendance_end_year": 2022,
            "attendance_end_month": 6,
        },
    ],
)
def test_invalid_month_or_reversed_range_is_rejected(
    client, catalog, user_factory, auth_headers, changes
):
    response = client.post(
        ME, headers=auth_headers(user_factory()), json=submission(catalog, **changes)
    )
    assert response.status_code == 422


def test_future_month_is_rejected(client, catalog, user_factory, auth_headers):
    now = datetime.now(timezone.utc)
    if now.month == 12:
        pytest.skip("No future month remains in this year")
    response = client.post(
        ME,
        headers=auth_headers(user_factory()),
        json=submission(
            catalog,
            attendance_start_year=now.year,
            attendance_start_month=now.month + 1,
            attendance_end_year=None,
            attendance_end_month=None,
        ),
    )
    assert response.status_code == 422 and "future" in response.text


def test_still_attending_can_omit_end_date(client, catalog, user_factory, auth_headers):
    link = create(
        client,
        catalog,
        user_factory(),
        auth_headers,
        attendance_end_year=None,
        attendance_end_month=None,
    )
    assert link["attendance_end_year"] is None and link["attendance_end_month"] is None


def test_existing_year_only_records_are_read_without_guessing_month(
    client, catalog, user_factory, auth_headers, db
):
    user = user_factory()
    link = create(client, catalog, user, auth_headers)
    stored = db.get(AcademicRecordLink, link["id"])
    stored.attendance_start_month = stored.attendance_end_month = None
    db.commit()
    response = client.get(f"{ME}/{link['id']}", headers=auth_headers(user))
    assert response.status_code == 200
    assert response.json()["attendance_start_year"] == 2018
    assert response.json()["attendance_start_month"] is None
