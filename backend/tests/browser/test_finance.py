"""Platform finance stays separate from the school's academic workspace."""
# ruff: noqa: F811 -- shared pytest fixtures

import os

import pytest

from app.models.user import UserRole
from tests.browser.test_workspace import browser, live_url, login  # noqa: F401
from tests.test_payments import gateway  # noqa: F401
from tests.test_platform_finance import paid, platform  # noqa: F401
from tests.test_upfront_checkout import upfront  # noqa: F401

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_BROWSER_TESTS") != "1", reason="Opt-in browser checks"
)


def test_platform_admin_can_pause_school_and_refund(
    browser, live_url, client, platform, gateway, catalog, user_factory
):
    paid(client, platform, gateway)
    admin = user_factory("finance-browser@example.com", role=UserRole.ADMIN)
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    login(page, live_url, admin.email)
    page.locator("#admin-tab").click()
    page.locator("#admin-institution").select_option(str(catalog["institution"].id))
    page.locator("#school-billing").wait_for(state="visible")
    page.locator("#school-billing-form [name=enabled]").uncheck()
    page.locator("#school-billing-form [name=reason]").fill("Pause this demo school")
    page.locator("#school-billing-form button").click()
    page.locator("#school-billing-status").filter(has_text="disabled").wait_for()
    page.locator("#finance-payments [name=reason]").fill(
        "School cannot provide the documents"
    )
    page.get_by_role("button", name="Save platform refund decision", exact=True).click()
    page.locator("#finance-payments").filter(has_text="Refund succeeded").wait_for()
    assert gateway.refunds
    assert "TEST" in page.locator("#finance-collections").inner_text()
    assert (
        "Refunds handled by TranscriptsKE"
        in page.locator("#finance-payments").inner_text()
    )
    assert not errors
    page.close()


def test_reviewed_unconfirmed_payment_returns_student_to_retry(
    browser, live_url, client, platform, gateway, user_factory
):
    from tests.test_orders import checked, submit
    from tests.test_payments import start

    submit(client, platform)
    gateway.fail_start = True
    previous = checked(start(client, platform, provider="mpesa"), 201)
    admin = user_factory(
        "payment-investigation-browser@example.com", role=UserRole.ADMIN
    )
    page = browser.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    login(page, live_url, admin.email)
    page.locator("#admin-tab").click()
    page.locator("#finance-payments [name=provider_case_reference]").fill(
        "SANDBOX-CASE-TEST"
    )
    page.locator("#finance-payments [name=evidence]").fill(
        "Test provider confirmed this request was rejected and no payment was received."
    )
    page.locator("#finance-payments [name=confirmed_no_payment]").check()
    page.get_by_role(
        "button", name="Confirm no payment and allow retry", exact=True
    ).click()
    page.locator("#notice").filter(has_text="customer can retry").wait_for()
    assert not errors
    page.close()
    gateway.fail_start = False
    student = browser.new_page()
    login(student, live_url, platform["user"].email)
    student.locator("nav [data-view=orders-view]").click()
    student.get_by_role("button", name="Continue payment", exact=True).click()
    retry = student.get_by_role("button", name="Retry payment", exact=True)
    retry.wait_for(state="visible")
    student.locator("#order-state button").click()
    assert student.locator("#student-payments [name=provider]").evaluate(
        "element => element === document.activeElement"
    )
    student.locator("#student-payments [name=phone]").fill("0712345678")
    retry.click()
    student.locator("#notice").filter(has_text="request saved").wait_for()
    attempts = checked(client.get(platform["pay_url"], headers=platform["headers"]))[
        "attempts"
    ]
    assert len(attempts) == 2 and attempts[-1]["id"] != previous["id"]
    assert attempts[-1]["status"] == "pending"
    student.close()
