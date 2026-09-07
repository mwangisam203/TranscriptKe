"""Optional real-browser checks. See README for browser installation and invocation."""

import os
import socket
import threading
import time

import pytest
import uvicorn
from sqlalchemy import select

from app.main import app
from app.models.academic import AcademicRecordLink
from tests.conftest import PASSWORD

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_BROWSER_TESTS") != "1",
    reason="Opt-in browser checks: RUN_BROWSER_TESTS=1",
)


@pytest.fixture
def live_url(client):
    # Reuse the isolated test database and fake email dependencies installed by client.
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [sock]}, daemon=True
    )
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started, "Test server did not start"
    yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    server.should_exit = True
    thread.join(timeout=10)
    sock.close()


@pytest.fixture
def browser():
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as driver:
        browser = driver.chromium.launch()
        yield browser
        browser.close()


def login(page, live_url, email):
    page.goto(live_url + "/workspace")
    page.locator("#login-form [name=email]").fill(email)
    page.locator("#login-form [name=password]").fill(PASSWORD)
    page.locator("#login-form button").click()
    page.locator("#workspace").wait_for(state="visible")
    page.locator("#notice").filter(has_text="Signed in.").wait_for()


def test_student_submission_staff_review_and_student_status(
    browser, live_url, catalog, user_factory, db
):
    student = user_factory()
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    login(page, live_url, student.email)
    page.select_option("#student-institution", str(catalog["institution"].id))
    page.locator(f"#student-service option[value='{catalog['service'].id}']").wait_for(
        state="attached"
    )
    page.select_option("#student-service", str(catalog["service"].id))
    assert "1,500.50" in page.locator("#service-summary").inner_text()
    page.locator("#record-form [name=admission_number]").fill("BROWSER/001")
    page.locator("#record-form [name=name_on_record]").fill("Jane Browser")
    page.locator("#record-form [name=program]").fill("Computer Science")
    page.locator("#record-form [name=attendance_start_year]").fill("2018")
    page.locator("#record-submit").click()
    page.locator("#my-links .badge.pending").wait_for()
    assert db.scalar(select(AcademicRecordLink)).status == "pending"
    page.locator("#logout").click()
    page.locator("#authentication").wait_for(state="visible")
    login(page, live_url, catalog["staff"].email)
    page.locator("#staff-tab").click()
    page.get_by_role("button", name="Open review", exact=True).click()
    page.locator("#decision-form").wait_for(state="visible")
    page.locator("#decision-form [name=decision]").select_option("matched")
    page.locator("#decision-form [name=student_message]").fill(
        "Your academic record has been confirmed."
    )
    page.locator("#decision-form [name=record_reference]").fill("ARCHIVE/BROWSER/001")
    page.locator("#decision-form [name=ownership_confirmed]").check()
    page.locator("#decision-form [name=internal_note]").fill(
        "Private registrar check against independently held enrollment information confirmed the applicant's identity."
    )
    page.locator("#decision-form button").click()
    page.locator("#notice").filter(has_text="Decision saved.").wait_for()
    page.locator("#logout").click()
    page.locator("#authentication").wait_for(state="visible")
    login(page, live_url, student.email)
    page.locator("#my-links .badge.matched").wait_for()
    page.get_by_role("button", name="View history", exact=True).click()
    page.locator("#student-history .history").first.wait_for()
    assert "Private registrar check" not in page.locator("body").inner_text()
    assert "ARCHIVE/BROWSER/001" not in page.locator("body").inner_text()
    screenshot = os.environ.get("WORKSPACE_SCREENSHOT")
    if screenshot:
        page.screenshot(path=screenshot, full_page=True)
    assert not errors
    page.close()


def test_manager_changes_service_and_policy_on_mobile(browser, live_url, catalog, db):
    page = browser.new_page(viewport={"width": 390, "height": 844})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    login(page, live_url, catalog["manager"].email)
    page.locator("#staff-tab").click()
    page.get_by_role("button", name="Edit service", exact=True).click()
    page.locator("#service-form [name=fee]").fill("2000.75")
    page.locator("#service-form button").click()
    page.locator("#notice").filter(has_text="Document service saved.").wait_for()
    db.refresh(catalog["service"])
    assert catalog["service"].fee_minor == 200075
    page.locator("#policy-form [name=accepting_requests]").uncheck()
    page.locator("#policy-form [name=student_instructions]").fill(
        "Demo submissions are temporarily paused."
    )
    page.locator("#policy-form button").click()
    page.locator("#notice").filter(has_text="Institution policy saved.").wait_for()
    db.refresh(catalog["policy"])
    assert not catalog["policy"].accepting_requests
    assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
    assert not errors
    page.close()
