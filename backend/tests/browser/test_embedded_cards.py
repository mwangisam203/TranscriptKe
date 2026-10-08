"""SDK stub verifies mount/cleanup; real card validation belongs to provider acceptance."""
# ruff: noqa: F811 -- shared pytest fixtures

import os

import pytest

from tests.browser.test_workspace import browser, live_url, login  # noqa: F401
from tests.test_embedded_cards import embedded  # noqa: F401
from tests.test_payments import gateway  # noqa: F401
from tests.test_upfront_checkout import upfront  # noqa: F401

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_BROWSER_TESTS") != "1", reason="Opt-in browser checks"
)


def test_card_fields_mount_and_are_destroyed_when_leaving(browser, live_url, embedded):
    w, _ = embedded
    page = browser.new_page()
    page.route(
        "https://js.stripe.com/v3/",
        lambda route: route.fulfill(
            content_type="application/javascript",
            body="window.Stripe = () => ({initEmbeddedCheckout: async () => ({mount: host => {window.cardMounted = true; host.textContent = 'Secure card fields mounted';}, destroy: () => {window.cardDestroyed = true;}})});",
        ),
    )
    login(page, live_url, w["user"].email)
    page.locator("nav [data-view=orders-view]").click()
    page.get_by_role("button", name="Open order", exact=True).click()
    page.get_by_role("button", name="Enter card details", exact=True).click()
    page.locator(".secure-card-fields").filter(
        has_text="Secure card fields mounted"
    ).wait_for()
    assert "Visa / Mastercard" in page.locator("#student-payments").inner_text()
    page.get_by_role("button", name="Back to my orders", exact=True).click()
    assert page.evaluate("window.cardDestroyed === true")
    page.close()
