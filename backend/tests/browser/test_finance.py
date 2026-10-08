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
