"""A confirmed unsuccessful payment can be retried with a new provider attempt."""
# ruff: noqa: F811 -- shared pytest fixtures

import os

import pytest

from tests.browser.test_workspace import browser, live_url, login  # noqa: F401
from tests.test_orders import checked, submit
from tests.test_payments import gateway, start  # noqa: F401
from tests.test_upfront_checkout import upfront  # noqa: F401

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_BROWSER_TESTS") != "1", reason="Opt-in browser checks"
)


@pytest.mark.parametrize(
    "provider,status", [("mpesa", "failed"), ("stripe", "expired")]
)
def test_retry_failed_or_expired_checkout(
    browser, live_url, client, upfront, gateway, catalog, db, provider, status
):
    catalog["service"].fee_minor = 500
    catalog["service"].version += 1
    db.commit()
    submit(client, upfront)
    previous = checked(start(client, upfront, provider=provider), 201)
    gateway.states[previous["id"]] = {"status": status, "refunded_minor": 0}
    checked(
        client.post(
            upfront["pay_url"] + f"/{previous['id']}/reconcile",
            headers=upfront["headers"],
        )
    )
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    login(page, live_url, upfront["user"].email)
    page.locator("nav [data-view=orders-view]").click()
    page.get_by_role("button", name="Open order", exact=True).click()
    retry = page.get_by_role("button", name="Retry payment", exact=True)
    retry.wait_for(state="visible")
    form = retry.locator("xpath=..")
    assert form.locator("[name=provider]").input_value() == provider
    if provider == "mpesa":
        form.locator("[name=phone]").fill("0712345678")
    with page.expect_response(
        lambda response: (
            response.request.method == "POST"
            and response.url.endswith(upfront["pay_url"])
        )
    ) as created:
        retry.click()
    assert created.value.status == 201
    page.locator("#notice").filter(has_text="request saved").wait_for()
    attempts = checked(client.get(upfront["pay_url"], headers=upfront["headers"]))[
        "attempts"
    ]
    assert len(attempts) == 2
    assert attempts[-1]["id"] != previous["id"]
    assert attempts[-1]["amount_minor"] == previous["amount_minor"] == 1000
    assert attempts[-1]["status"] == "pending"
    assert page.get_by_role("button", name="Retry payment", exact=True).count() == 0
    assert page.get_by_role("button", name="Start payment", exact=True).count() == 0
    assert (
        checked(client.get(upfront["url"], headers=upfront["headers"]))["submitted_at"]
        is None
    )
    page.close()


def test_pending_screen_detects_failure_and_offers_retry(
    browser, live_url, client, upfront, gateway
):
    submit(client, upfront)
    previous = checked(start(client, upfront, provider="mpesa"), 201)
    # No callback or manual reconcile: the browser must discover this outcome.
    gateway.states[previous["id"]] = {"status": "failed", "refunded_minor": 0}
    page = browser.new_page()
    login(page, live_url, upfront["user"].email)
    page.locator("nav [data-view=orders-view]").click()
    page.get_by_role(
        "button",
        name="Open " + upfront["order"]["reference"] + ": continue payment",
        exact=True,
    ).click()
    retry = page.get_by_role("button", name="Retry payment", exact=True)
    retry.wait_for(state="visible")
    assert page.get_by_role(
        "button", name="Choose method and retry", exact=True
    ).is_visible()
    page.get_by_role("button", name="Choose method and retry", exact=True).click()
    form = retry.locator("xpath=..")
    assert form.locator("[name=provider]").evaluate(
        "element => element === document.activeElement"
    )
    assert (
        form.locator("[name=provider]")
        .locator("xpath=..")
        .locator(".required-marker")
        .count()
        == 1
    )
    assert (
        form.locator("[name=phone]")
        .locator("xpath=..")
        .locator(".required-marker")
        .count()
        == 1
    )
    form.locator("[name=provider]").select_option("stripe")
    page.wait_for_function(
        "!document.querySelector('#student-payments [name=phone]').required"
    )
    assert (
        form.locator("[name=phone]")
        .locator("xpath=..")
        .locator(".required-marker")
        .count()
        == 0
    )
    # Status checks do not create another financial attempt or release the order.
    assert (
        len(
            checked(client.get(upfront["pay_url"], headers=upfront["headers"]))[
                "attempts"
            ]
        )
        == 1
    )
    assert (
        checked(client.get(upfront["url"], headers=upfront["headers"]))["submitted_at"]
        is None
    )
    page.close()


def test_unconfirmed_payment_cannot_start_another_charge(
    browser, live_url, client, upfront, gateway
):
    submit(client, upfront)
    previous = checked(start(client, upfront, provider="mpesa"), 201)
    page = browser.new_page()
    login(page, live_url, upfront["user"].email)
    page.locator("nav [data-view=orders-view]").click()
    page.get_by_role("button", name="Continue payment", exact=True).click()
    page.get_by_text(
        "Waiting for confirmation. Payment status is checked automatically", exact=False
    ).wait_for()
    assert page.get_by_role("button", name="Retry payment", exact=True).count() == 0
    assert page.get_by_role("button", name="Start payment", exact=True).count() == 0
    page.locator("#order-state button").click()
    assert page.locator("#student-payments h3").evaluate(
        "element => element === document.activeElement"
    )
    page.locator("nav [data-view=account-view]").click()
    assert page.evaluate("P_refreshTimer === null")
    attempts = checked(client.get(upfront["pay_url"], headers=upfront["headers"]))[
        "attempts"
    ]
    assert [attempt["id"] for attempt in attempts] == [previous["id"]]
    page.close()


def test_missing_provider_reference_opens_payment_help(
    browser, live_url, client, upfront, gateway
):
    submit(client, upfront)
    gateway.fail_start = True
    payment = checked(start(client, upfront, provider="mpesa"), 201)
    page = browser.new_page()
    login(page, live_url, upfront["user"].email)
    page.locator("nav [data-view=orders-view]").click()
    page.get_by_role("button", name="Continue payment", exact=True).click()
    help_button = page.get_by_role("button", name="Get payment help", exact=True)
    help_button.wait_for(state="visible")
    assert (
        page.get_by_role("button", name="Check payment status", exact=True).count() == 0
    )
    assert page.get_by_role("button", name="Retry payment", exact=True).count() == 0
    help_button.click()
    page.locator("#account-support-dialog").wait_for(state="visible")
    assert payment["id"] in page.locator("#payment-support-context").inner_text()
    assert page.evaluate("P_refreshTimer === null")
    page.locator("#close-account-support").click()
    page.locator("#payment-support-context").wait_for(state="detached")
    assert page.locator("#payment-support-context").count() == 0
    page.close()


def test_status_error_is_inline_and_retry_appears_after_confirmation(
    browser, live_url, client, upfront, gateway
):
    submit(client, upfront)
    payment = checked(start(client, upfront, provider="mpesa"), 201)
    gateway.fail_observe = True
    page = browser.new_page()
    login(page, live_url, upfront["user"].email)
    page.locator("nav [data-view=orders-view]").click()
    page.get_by_role("button", name="Continue payment", exact=True).click()
    button = page.get_by_role("button", name="Check payment status", exact=True)
    button.click()
    page.locator("#student-payments .payment-feedback[role=alert]").filter(
        has_text="verification is unavailable"
    ).wait_for()
    assert button.is_enabled()
    gateway.fail_observe = False
    gateway.states[payment["id"]] = {"status": "failed", "refunded_minor": 0}
    button.click()
    page.get_by_role("button", name="Retry payment", exact=True).wait_for(
        state="visible"
    )
    assert (
        page.get_by_role("button", name="Check payment status", exact=True).count() == 0
    )
    page.close()
