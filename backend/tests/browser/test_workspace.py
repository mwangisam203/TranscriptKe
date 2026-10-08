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
from tests.test_issuance import PDF, ready, registrar  # noqa: F401 -- issuance fixtures
from tests.test_operations import operations  # noqa: F401 -- pilot operations fixture
from tests.test_orders import workflow  # noqa: F401 -- shared ordering setup
from tests.test_payments import (  # noqa: F401 -- browser payment fixtures
    gateway,
    payable,
)
from tests.test_upfront_checkout import (
    upfront,  # noqa: F401 -- upfront checkout fixture
)

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
    page.select_option("#record-currently-enrolled", "yes")
    page.locator("#record-form [name=attendance_start_date]").fill("2018-09")
    page.locator("#record-submit").click()
    page.locator("#my-links .badge.pending").wait_for()
    assert db.scalar(select(AcademicRecordLink)).status == "pending"
    if page.locator("#logout").is_hidden():
        page.locator("#account-menu-toggle").click()
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
    if page.locator("#logout").is_hidden():
        page.locator("#account-menu-toggle").click()
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
    assert page.evaluate(
        "() => document.documentElement.scrollWidth <= window.innerWidth"
    )
    assert not errors
    page.close()


@pytest.mark.parametrize("width", [1280, 390])
def test_order_submission_questions_and_cancellation(
    browser,
    live_url,
    workflow,  # noqa: F811 -- imported pytest fixture
    catalog,
    width,
):
    w = workflow
    page = browser.new_page(viewport={"width": width, "height": 900})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    login(page, live_url, w["user"].email)
    page.locator("nav [data-view=orders-view]").click()
    page.get_by_role("button", name="Open order", exact=True).click()
    page.locator("#order-editor").wait_for(state="visible")
    page.locator("#order-editor [name=purpose]").fill("Admission to graduate school")
    page.get_by_role(
        "button", name="Continue to review and consent", exact=True
    ).click()
    page.locator("#order-quote").wait_for(state="visible")
    page.locator("#order-file").set_input_files(
        {
            "name": "instructions.txt",
            "mimeType": "text/plain",
            "buffer": b"Please include all semesters.",
        }
    )
    page.locator("#order-attachment-form button").click()
    page.locator("#notice").filter(has_text="Attachment added.").wait_for()
    page.locator("#quote-order").click()
    page.locator("#order-consent-checkbox").wait_for()
    assert "3,001.00" in page.locator("#order-quote").inner_text()
    assert "instructions.txt" in page.locator("#order-quote").inner_text()
    page.locator("#order-signature").scroll_into_view_if_needed()
    pad = page.locator("#order-signature").bounding_box()
    page.mouse.move(pad["x"] + 20, pad["y"] + 25)
    page.mouse.down()
    page.mouse.move(pad["x"] + 100, pad["y"] + 65, steps=8)
    page.mouse.move(pad["x"] + 180, pad["y"] + 30, steps=8)
    page.mouse.up()
    page.locator("#order-consent-checkbox").check()
    page.locator("#order-final-submit").click()
    page.locator("#notice").filter(has_text="Order submitted.").wait_for()
    assert "submitted" in page.locator("#order-state").inner_text()
    assert page.evaluate(
        "() => document.documentElement.scrollWidth <= window.innerWidth"
    )
    if page.locator("#logout").is_hidden():
        page.locator("#account-menu-toggle").click()
    page.locator("#logout").click()
    page.locator("#authentication").wait_for(state="visible")
    login(page, live_url, catalog["staff"].email)
    page.locator("#staff-tab").click()
    page.get_by_role("button", name="Review order", exact=True).click()
    page.locator("#staff-order-message-form [name=body]").fill(
        "Confirm the graduation session."
    )
    page.locator("#staff-order-message-form [name=requires_response]").check()
    page.locator("#staff-order-message-form button").click()
    page.locator("#notice").filter(has_text="Student message sent.").wait_for()
    if page.locator("#logout").is_hidden():
        page.locator("#account-menu-toggle").click()
    page.locator("#logout").click()
    page.locator("#authentication").wait_for(state="visible")
    login(page, live_url, w["user"].email)
    page.locator("nav [data-view=orders-view]").click()
    assert "1 question(s)" in page.locator("#order-list").inner_text()
    page.get_by_role("button", name="Open order", exact=True).click()
    page.locator("#order-message-form").wait_for(state="visible")
    page.locator("#order-message-form [name=in_reply_to_id]").select_option(index=1)
    page.locator("#order-message-form [name=body]").fill("December 2026.")
    page.locator("#order-message-form button").click()
    page.locator("#notice").filter(has_text="Message sent.").wait_for()
    assert "Response received." in page.locator("#order-conversation").inner_text()
    page.locator("#order-cancel-form [name=reason]").fill("No longer applying.")
    page.locator("#order-cancel-form button").click()
    page.locator("#notice").filter(has_text="Cancellation recorded.").wait_for()
    if page.locator("#logout").is_hidden():
        page.locator("#account-menu-toggle").click()
    page.locator("#logout").click()
    page.locator("#authentication").wait_for(state="visible")
    login(page, live_url, catalog["staff"].email)
    page.locator("#staff-tab").click()
    page.get_by_role("button", name="Review order", exact=True).click()
    page.locator("#staff-order-cancel-form [name=decision]").select_option("approved")
    page.locator("#staff-order-cancel-form [name=reason]").fill(
        "Cancelled before processing."
    )
    page.locator("#staff-order-cancel-form button").click()
    page.locator("#notice").filter(has_text="Cancellation decision saved.").wait_for()
    assert "cancelled" in page.locator("#staff-order-title").inner_text()
    assert page.evaluate(
        "() => document.documentElement.scrollWidth <= window.innerWidth"
    )
    if width == 1280:
        page.screenshot(path="/tmp/transcriptske-milestone3.png", full_page=True)
    assert not errors
    page.close()


@pytest.mark.parametrize("width", [1280, 390])
def test_registrar_review_holds_and_student_progress(
    browser,
    live_url,
    workflow,  # noqa: F811 -- imported pytest fixture
    catalog,
    client,
    width,
):
    from tests.test_orders import submit

    submit(client, workflow)
    page = browser.new_page(viewport={"width": width, "height": 900})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    login(page, live_url, catalog["staff"].email)
    page.locator("#staff-tab").click()
    page.get_by_role("button", name="Review order", exact=True).click()
    page.get_by_role("button", name="Assign to me", exact=True).click()
    page.locator("#notice").filter(has_text="Registrar update saved.").wait_for()
    decision_form = page.locator("#registrar-workspace form").filter(
        has=page.get_by_role("heading", name="Document decision", exact=True)
    )
    decision_form.locator("[name=student_message]").fill(
        "Your request has been approved."
    )
    decision_form.locator("[name=internal_note]").fill(
        "PRIVATE registrar archive evidence."
    )
    decision_form.get_by_role("button").click()
    page.locator("#registrar-workspace").get_by_text(
        "Official transcript: processing", exact=True
    ).wait_for()
    hold_form = page.locator("#registrar-workspace form").filter(
        has=page.get_by_role("heading", name="Place an order hold", exact=True)
    )
    hold_form.locator("[name=student_message]").fill("Please confirm clearance.")
    hold_form.locator("[name=internal_note]").fill("PRIVATE financial ledger review.")
    hold_form.get_by_role("button").click()
    page.get_by_text("Active hold: Please confirm clearance.", exact=True).wait_for()
    resolve_form = page.locator("#registrar-workspace form").filter(
        has=page.get_by_role("heading", name="Resolve hold", exact=True)
    )
    resolve_form.locator("[name=student_message]").fill("Clearance confirmed.")
    resolve_form.locator("[name=internal_note]").fill(
        "PRIVATE clearance source checked."
    )
    resolve_form.get_by_role("button").click()
    page.get_by_text("Resolved hold: Please confirm clearance.", exact=True).wait_for()
    decision_form.locator("[name=decision]").select_option("ready")
    decision_form.locator("[name=student_message]").fill(
        "Preparation is complete; release checks remain."
    )
    decision_form.locator("[name=internal_note]").fill(
        "PRIVATE registrar preparation evidence."
    )
    decision_form.get_by_role("button").click()
    page.locator("#registrar-workspace").get_by_text(
        "Official transcript: ready", exact=True
    ).wait_for()
    assert (
        "Verified payment is required"
        in page.locator("#registrar-workspace").inner_text()
    )
    assert page.evaluate(
        "() => document.documentElement.scrollWidth <= window.innerWidth"
    )
    if page.locator("#logout").is_hidden():
        page.locator("#account-menu-toggle").click()
    page.locator("#logout").click()
    page.locator("#authentication").wait_for(state="visible")
    login(page, live_url, workflow["user"].email)
    page.locator("nav [data-view=orders-view]").click()
    page.get_by_role("button", name="Open order", exact=True).click()
    page.locator("#student-fulfillment").get_by_text(
        "Official transcript: ready", exact=True
    ).wait_for()
    assert "Clearance confirmed." in page.locator("#student-fulfillment").inner_text()
    assert "PRIVATE" not in page.locator("body").inner_text()
    assert not errors
    page.close()


@pytest.mark.parametrize("width", [1280, 390])
def test_payment_receipt_and_manager_refund(
    browser,
    live_url,
    payable,  # noqa: F811 -- imported fixture
    gateway,  # noqa: F811 -- imported fixture
    catalog,
    width,
):
    page = browser.new_page(viewport={"width": width, "height": 900})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    login(page, live_url, payable["user"].email)
    page.locator("nav [data-view=orders-view]").click()
    page.get_by_role("button", name="Open order", exact=True).click()
    page.get_by_role("button", name="Start payment", exact=True).click()
    page.get_by_role("link", name="Open secure card checkout", exact=True).wait_for()
    identifier = gateway.starts[0]
    gateway.states[identifier] = {
        "status": "succeeded",
        "refunded_minor": 0,
        "transaction_reference": "pi_browser",
    }
    page.get_by_role("button", name="Check payment status", exact=True).click()
    page.locator("#notice").filter(
        has_text="Payment status checked with the provider."
    ).wait_for()
    assert "Payment: paid" in page.locator("#order-state").inner_text()
    page.get_by_role("button", name="View payment receipt", exact=True).click()
    page.get_by_text("TEST Payment receipt", exact=True).wait_for()
    form = page.locator("#student-payments form").filter(
        has=page.get_by_role("heading", name="Request a full refund", exact=True)
    )
    form.locator("[name=reason]").fill("No longer need this order.")
    form.get_by_role("button", name="Request refund", exact=True).click()
    page.locator("#notice").filter(has_text="Refund request sent").wait_for()
    assert page.evaluate(
        "() => document.documentElement.scrollWidth <= window.innerWidth"
    )
    if page.locator("#logout").is_hidden():
        page.locator("#account-menu-toggle").click()
    page.locator("#logout").click()
    page.locator("#authentication").wait_for(state="visible")
    login(page, live_url, catalog["manager"].email)
    page.locator("#staff-tab").click()
    page.get_by_role("button", name="Review order", exact=True).click()
    page.get_by_role("button", name="Save refund decision", exact=True).click()
    page.locator("#notice").filter(has_text="Refund decision recorded.").wait_for()
    assert "cancelled" in page.locator("#staff-order-title").inner_text()
    assert "refunded" in page.locator("#staff-payments").inner_text()
    assert page.evaluate(
        "() => document.documentElement.scrollWidth <= window.innerWidth"
    )
    assert not errors
    page.close()


@pytest.mark.parametrize("width", [1280, 390])
def test_upload_issue_and_recipient_download(
    browser,
    live_url,
    ready,  # noqa: F811 -- imported fixture
    catalog,
    mailer,
    width,  # noqa: F811
):
    page = browser.new_page(viewport={"width": width, "height": 900})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    login(page, live_url, catalog["staff"].email)
    page.locator("#staff-tab").click()
    page.get_by_role("button", name="Review order", exact=True).click()
    page.locator("#staff-documents input[type=file]").set_input_files(
        {"name": "transcript.pdf", "mimeType": "application/pdf", "buffer": PDF}
    )
    page.get_by_role("button", name="Upload PDF", exact=True).click()
    page.locator("#notice").filter(has_text="PDF uploaded and scanned.").wait_for()
    page.locator("#staff-documents [name=attested]").check()
    page.locator("#staff-documents [name=internal_note]").fill(
        "Original and recipient verified."
    )
    page.get_by_role("button", name="Issue document", exact=True).click()
    page.get_by_role(
        "button", name="Send recipient notification", exact=True
    ).wait_for()
    with page.expect_response(
        lambda response: (
            response.url.endswith("/notify") and response.request.method == "POST"
        )
    ) as notified:
        page.get_by_role(
            "button", name="Send recipient notification", exact=True
        ).click()
    assert notified.value.status == 200, notified.value.text()
    page.locator("#staff-documents").filter(has_text="DEMO").wait_for()
    page.wait_for_function(
        "() => !document.querySelector('#staff-documents button[disabled]')"
    )
    # Wait for the asynchronous notification API to complete before reading fake mail.
    page.locator("#notice").filter(has_text="Document update saved.").wait_for()
    notification = next(
        m for m in reversed(mailer.messages) if m["purpose"] == "document_notification"
    )
    assert page.evaluate(
        "() => document.documentElement.scrollWidth <= window.innerWidth"
    )
    page.goto(live_url + "/recipient#" + notification["delivery_id"])
    page.locator("[name=email]").fill("admissions@example.com")
    page.get_by_role("button", name="Send access code", exact=True).click()
    page.locator("#recipient-notice").filter(
        has_text="an access code will be sent"
    ).wait_for()
    page.locator("[name=code]").fill(mailer.token("document_access"))
    with page.expect_download() as download:
        page.get_by_role("button", name="Download document", exact=True).click()
    result = download.value
    assert result.suggested_filename.startswith("DEMO-document-")
    from pathlib import Path

    assert Path(result.path()).read_bytes() == PDF
    assert page.evaluate(
        "() => document.documentElement.scrollWidth <= window.innerWidth"
    )
    assert not errors
    page.close()


@pytest.mark.parametrize("width", [1280, 390])
def test_manager_operations_dashboard_and_private_follow_up(
    browser,
    live_url,
    operations,  # noqa: F811 -- imported fixture
    catalog,
    db,
    width,  # noqa: F811
):
    from datetime import timedelta

    from app.models.operations import OperationsCase
    from app.models.orders import Order
    from app.services.orders import utcnow

    w = operations
    db.get(Order, w["order"]["id"]).processing_due_at = utcnow() - timedelta(days=1)
    db.commit()
    page = browser.new_page(viewport={"width": width, "height": 900})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    login(page, live_url, catalog["manager"].email)
    page.locator("#staff-tab").click()
    page.locator("#operations-queue h4").filter(
        has_text=w["order"]["reference"]
    ).wait_for()
    assert "Past planning target (1)" in page.locator("#operations-queue").inner_text()
    page.get_by_role("button", name="Review follow-up", exact=True).click()
    page.locator("#operations-case [name=note]").fill(
        "PRIVATE: manager will contact the registrar."
    )
    page.get_by_role("button", name="Save operations review", exact=True).click()
    page.locator("#notice").filter(has_text="Operations review saved.").wait_for()
    assert (
        db.get(OperationsCase, w["order"]["id"]).note
        == "PRIVATE: manager will contact the registrar."
    )
    page.get_by_role("button", name="Open order workflow", exact=True).click()
    page.locator("#staff-order-title").filter(
        has_text=w["order"]["reference"]
    ).wait_for()
    assert page.evaluate(
        "() => document.documentElement.scrollWidth <= window.innerWidth"
    )
    if page.locator("#logout").is_hidden():
        page.locator("#account-menu-toggle").click()
    page.locator("#logout").click()
    page.locator("#authentication").wait_for(state="visible")
    assert page.locator("#operations-case").inner_text() == ""
    login(page, live_url, catalog["staff"].email)
    page.locator("#staff-tab").click()
    assert page.locator("#operations-panel").is_hidden()
    assert not errors
    page.close()


def test_refresh_keeps_session_and_recovers_incomplete_order(
    browser,
    live_url,
    workflow,  # noqa: F811
):
    page = browser.new_page(viewport={"width": 1100, "height": 900})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    w = workflow
    login(page, live_url, w["user"].email)
    page.locator("nav [data-view=orders-view]").click()
    page.get_by_role("button", name="Open order", exact=True).click()
    page.locator("#order-editor [name=purpose]").fill("Draft admission request")
    page.locator("#order-editor [name=recipient_name]").first.fill("")
    page.locator("#order-draft-state").filter(
        has_text="Draft saved automatically"
    ).wait_for()
    page.reload()
    page.locator("#notice").filter(has_text="Session restored.").wait_for()
    page.locator("#order-draft-state").filter(
        has_text="Unfinished changes restored"
    ).wait_for()
    assert (
        page.locator("#order-editor [name=purpose]").input_value()
        == "Draft admission request"
    )
    assert page.locator("#order-editor [name=recipient_name]").first.input_value() == ""
    assert page.locator("#authentication").is_hidden()
    assert not page.evaluate(
        "() => Object.values(localStorage).some(v => v.includes('access_token'))"
    )
    page.locator("#order-editor [name=recipient_name]").first.fill("Receiving office")
    page.locator("#quote-order").click()
    page.locator("#order-consent-checkbox").check()
    page.locator("#order-final-submit").click()
    page.locator("#notice").filter(has_text="Draw your signature").wait_for()
    assert "draft" in page.locator("#order-state").inner_text()
    assert not errors
    if page.locator("#logout").is_hidden():
        page.locator("#account-menu-toggle").click()
    page.locator("#logout").click()
    page.locator("#authentication").wait_for(state="visible")
    page.reload()
    page.locator("#authentication").wait_for(state="visible")
    assert page.locator("#workspace").is_hidden()
    page.close()


def test_typed_attendance_and_national_id_draft_restored(
    browser, live_url, catalog, user_factory
):
    page = browser.new_page()
    user = user_factory()
    login(page, live_url, user.email)
    page.select_option("#student-institution", str(catalog["institution"].id))
    page.locator(f"#student-service option[value='{catalog['service'].id}']").wait_for(
        state="attached"
    )
    page.select_option("#student-service", str(catalog["service"].id))
    page.select_option("#record-lookup-method", "identity")
    page.locator("#record-form [name=id_number]").fill("1234567")
    page.locator("#record-form [name=name_on_record]").fill("Unfinished Name")
    page.select_option("#record-currently-enrolled", "yes")
    dates = page.locator("#record-form [name=attendance_start_date]")
    assert dates.get_attribute("type") == "text"
    dates.fill("2020-09")
    page.locator("#enrollment-draft-state").filter(
        has_text="Draft saved automatically"
    ).wait_for()
    page.reload()
    page.locator("#notice").filter(has_text="Session restored.").wait_for()
    assert (
        page.locator("#record-form [name=name_on_record]").input_value()
        == "Unfinished Name"
    )
    assert page.locator("#record-form [name=id_number]").input_value() == "1234567"
    assert (
        page.locator("#record-form [name=attendance_start_date]").input_value()
        == "2020-09"
    )
    assert (
        page.locator("#record-form [name=id_number]").get_attribute("pattern")
        == "[0-9]{7,8}"
    )
    page.close()


def test_plain_text_server_error_shows_useful_message(browser, live_url, user_factory):
    page = browser.new_page()
    user = user_factory()
    login(page, live_url, user.email)
    page.route(
        "**/api/v1/me/academic-record-links?*",
        lambda route: route.fulfill(
            status=500, content_type="text/plain", body="Internal Server Error"
        ),
    )
    page.locator("#refresh-links").click()
    page.locator("#notice").filter(has_text="HTTP 500").wait_for()
    assert "Unexpected token" not in page.locator("#notice").inner_text()
    assert "backend terminal" in page.locator("#notice").inner_text()
    page.close()


def test_history_reopens_enrollment_and_continues_existing_draft(
    browser,
    live_url,
    workflow,  # noqa: F811
):
    w = workflow
    page = browser.new_page(viewport={"width": 390, "height": 950})
    login(page, live_url, w["user"].email)
    page.locator("#record-form [name=name_on_record]").fill(
        "Separate unfinished enrollment"
    )
    page.locator("#enrollment-draft-state").filter(
        has_text="Draft saved automatically"
    ).wait_for()
    page.get_by_role("button", name="View history", exact=True).click()
    page.locator("#record-form-title").filter(
        has_text="enrollment is confirmed"
    ).wait_for()
    assert (
        page.locator("#record-form [name=name_on_record]").input_value()
        == w["link"]["name_on_record"]
    )
    assert page.locator("#record-form [name=name_on_record]").is_disabled()
    assert page.locator("#record-submit").is_hidden()
    assert page.locator("#student-history .history").count() > 0
    page.locator("#record-continue-order").click()
    page.locator("#order-title").filter(has_text=w["order"]["reference"]).wait_for()
    assert page.locator("#orders-view").is_visible()
    assert page.locator("#order-editor").is_visible()
    page.locator("#order-editor [name=purpose]").fill("Continue this saved application")
    page.locator("#order-draft-state").filter(
        has_text="Draft saved automatically"
    ).wait_for()
    page.get_by_role("button", name="Back to my orders", exact=True).click()
    page.get_by_role("button", name="Continue draft", exact=True).click()
    page.locator("#order-draft-state").filter(
        has_text="Unfinished changes restored"
    ).wait_for()
    assert (
        page.locator("#order-editor [name=purpose]").input_value()
        == "Continue this saved application"
    )
    page.locator("nav [data-view=student-view]").click()
    page.locator("#resume-enrollment-draft").click()
    page.locator("#enrollment-draft-state").filter(
        has_text="Saved enrollment draft restored"
    ).wait_for()
    assert (
        page.locator("#record-form [name=name_on_record]").input_value()
        == "Separate unfinished enrollment"
    )
    assert page.locator("#record-form [name=name_on_record]").is_enabled()
    page.close()


def test_history_waits_for_review_then_allows_requested_updates(
    browser, live_url, client, catalog, user_factory, auth_headers
):
    from tests.test_academic_records import create, decision, review_url

    user = user_factory()
    link = create(client, catalog, user, auth_headers)
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    login(page, live_url, user.email)
    page.get_by_role("button", name="View history", exact=True).click()
    page.locator("#record-form-title").filter(has_text="being reviewed").wait_for()
    assert page.locator("#record-form [name=name_on_record]").is_disabled()
    assert page.locator("#record-submit").is_hidden()
    response = client.post(
        review_url(catalog, link["id"]) + "/decisions",
        headers=auth_headers(catalog["staff"]),
        json=decision(outcome="needs_information"),
    )
    assert response.status_code == 200
    page.locator("#refresh-links").click()
    page.locator("#my-links .badge.needs_information").wait_for()
    page.get_by_role("button", name="View history", exact=True).click()
    page.locator("#record-form-title").filter(
        has_text="Continue your enrollment"
    ).wait_for()
    assert page.locator("#record-form [name=name_on_record]").is_enabled()
    page.locator("#record-form [name=name_on_record]").fill("Jane Updated")
    page.select_option("#record-currently-enrolled", "no")
    page.locator("#record-submit").click()
    page.locator("#my-links .badge.pending").wait_for()
    page.close()


def test_mpesa_sandbox_checkout_explains_prompt_and_checks_result(
    browser,
    live_url,
    payable,  # noqa: F811
    gateway,  # noqa: F811
):
    page = browser.new_page(viewport={"width": 1440, "height": 950})
    login(page, live_url, payable["user"].email)
    page.locator("nav [data-view=orders-view]").click()
    page.get_by_role("button", name="Open order", exact=True).click()
    page.locator("#student-payments select[name=provider]").select_option("mpesa")
    page.locator("#student-payments").filter(
        has_text="M-Pesa sandbox: use the test number"
    ).wait_for()
    page.locator("#student-payments input[name=phone]").fill("0712345678")
    page.get_by_role("button", name="Start payment", exact=True).click()
    page.locator("#notice").filter(has_text="Sandbox M-Pesa request saved").wait_for()
    page.locator("#student-payments").filter(
        has_text="Sandbox STK request accepted"
    ).wait_for()
    assert len(gateway.starts) == 1
    assert page.locator("#student-payments input[name=phone]").count() == 0
    assert page.get_by_role("button", name="Start payment", exact=True).count() == 0
    identifier = gateway.starts[0]
    gateway.states[identifier] = {
        "status": "succeeded",
        "refunded_minor": 0,
        "transaction_reference": "TESTMPESA001",
    }
    page.get_by_role("button", name="Check payment status", exact=True).click()
    page.get_by_role("button", name="View payment receipt", exact=True).wait_for()
    assert len(gateway.starts) == 1
    page.close()


@pytest.mark.parametrize("width", [1280, 390])
def test_checkout_before_review_releases_only_after_confirmed_payment(
    browser,
    live_url,
    upfront,  # noqa: F811
    gateway,  # noqa: F811
    width,
):
    page = browser.new_page(viewport={"width": width, "height": 950})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    login(page, live_url, upfront["user"].email)
    page.get_by_role("button", name="Continue to document request", exact=True).click()
    page.locator("#quote-order").click()
    page.locator("#order-consent-checkbox").wait_for()
    page.locator("#order-signature").scroll_into_view_if_needed()
    pad = page.locator("#order-signature").bounding_box()
    page.mouse.move(pad["x"] + 20, pad["y"] + 25)
    page.mouse.down()
    page.mouse.move(pad["x"] + 100, pad["y"] + 65, steps=8)
    page.mouse.move(pad["x"] + 180, pad["y"] + 30, steps=8)
    page.mouse.up()
    page.locator("#order-consent-checkbox").check()
    page.get_by_role("button", name="Continue to payment", exact=True).click()
    page.locator("#notice").filter(has_text="Checkout saved privately").wait_for()
    assert "awaiting payment" in page.locator("#order-state").inner_text()
    page.locator("#student-payments select[name=provider]").select_option("mpesa")
    page.locator("#student-payments input[name=phone]").fill("0712345678")
    page.get_by_role("button", name="Start payment", exact=True).click()
    page.locator("#notice").filter(has_text="Sandbox M-Pesa request saved").wait_for()
    assert "awaiting payment" in page.locator("#order-state").inner_text()
    page.reload()
    page.locator("#order-detail").wait_for(state="visible")
    assert "awaiting payment" in page.locator("#order-state").inner_text()
    assert len(gateway.starts) == 1
    gateway.states[gateway.starts[0]] = {
        "status": "succeeded",
        "transaction_reference": "SANDBOXBROWSER1",
        "refunded_minor": 0,
    }
    page.get_by_role("button", name="Check payment status", exact=True).click()
    page.locator("#notice").filter(has_text="Payment status checked").wait_for()
    assert "Status: submitted" in page.locator("#order-state").inner_text()
    assert "Payment: paid" in page.locator("#order-state").inner_text()
    assert len(gateway.starts) == 1
    assert not errors
    page.close()


def test_saving_enrollment_continues_directly_to_private_checkout(
    browser,
    live_url,
    user_factory,
    catalog,
    auth_headers,
    client,
    monkeypatch,
    gateway,  # noqa: F811
    db,
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "PAYMENT_COLLECTION_POLICY", "before_review")
    student = user_factory()
    page = browser.new_page(viewport={"width": 390, "height": 950})
    login(page, live_url, student.email)
    page.select_option("#student-institution", str(catalog["institution"].id))
    page.locator(f"#student-service option[value='{catalog['service'].id}']").wait_for(
        state="attached"
    )
    page.select_option("#student-service", str(catalog["service"].id))
    page.locator("#record-form [name=admission_number]").fill("CHECKOUT/001")
    page.locator("#record-form [name=name_on_record]").fill("Checkout Student")
    page.locator("#record-form [name=program]").fill("Computer Science")
    page.select_option("#record-currently-enrolled", "yes")
    page.locator("#record-form [name=attendance_start_date]").fill("2018-09")
    page.get_by_role(
        "button", name="Continue to documents and destination", exact=True
    ).click()
    page.locator("#notice").filter(has_text="Enrollment saved privately").wait_for()
    assert page.locator("#orders-view").is_visible()
    link = db.scalar(select(AcademicRecordLink))
    assert link.checkout_required and link.status == "pending"
    assert page.locator("#order-record").input_value() == str(link.id)
    response = client.get(
        f"/api/v1/staff/institutions/{catalog['institution'].id}/record-matches/{link.id}",
        headers=auth_headers(catalog["staff"]),
    )
    assert response.status_code == 404
    assert not gateway.starts
    page.close()
