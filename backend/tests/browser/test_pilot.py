"""Real browser coverage for manager submissions and independent admin decisions."""

# ruff: noqa: F811
import os

import pytest

from tests.browser.test_workspace import browser, live_url, login  # noqa: F401
from tests.test_pilot import EVIDENCE, REASON, pilot  # noqa: F401

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_BROWSER_TESTS") != "1", reason="Opt-in browser checks"
)


@pytest.mark.parametrize("width", [1280, 390])
def test_manager_pilot_submission_and_admin_review(
    browser, live_url, catalog, pilot, width
):
    page = browser.new_page(viewport={"width": width, "height": 900})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    login(page, live_url, catalog["manager"].email)
    page.locator("#staff-tab").click()
    panel = page.locator("#pilot-staff")
    panel.wait_for(state="visible")
    onboarding = panel.locator("form").filter(
        has=page.get_by_role(
            "heading", name="Prepare institution onboarding", exact=True
        )
    )
    for key, value in EVIDENCE.items():
        onboarding.locator(f"[name={key}]").fill(value)
    onboarding.get_by_role(
        "button", name="Save onboarding evidence", exact=True
    ).click()
    page.locator("#notice").filter(has_text="Onboarding evidence saved.").wait_for()
    panel.get_by_role("button", name="Submit onboarding for review", exact=True).click()
    page.locator("#notice").filter(has_text="Onboarding submitted").wait_for()
    evaluation = panel.locator("form").filter(
        has=page.get_by_role("heading", name="Evaluate a pilot cohort", exact=True)
    )
    evaluation.get_by_role("button", name="Preview pilot evidence", exact=True).click()
    evaluation.locator(".pilot-preview").filter(
        has_text="Demo activity cannot establish live pilot acceptance."
    ).wait_for()
    for key, value in EVIDENCE.items():
        evaluation.locator(f"[name={key}]").fill(value)
    evaluation.locator("[name=findings]").fill(REASON)
    evaluation.get_by_role("button", name="Submit pilot evaluation", exact=True).click()
    page.locator("#notice").filter(has_text="immutable evidence snapshot").wait_for()
    page.locator("#logout").click()
    page.locator("#authentication").wait_for(state="visible")
    assert page.locator("#pilot-staff").inner_text() == ""
    login(page, live_url, pilot["admin_user"].email)
    page.locator("#admin-tab").click()
    page.select_option("#admin-institution", str(pilot["id"]))
    admin = page.locator("#pilot-admin")
    form = admin.locator("form").filter(
        has=page.get_by_role("heading", name="Review onboarding", exact=True)
    )
    form.locator("[name=decision]").select_option("approved")
    form.locator("[name=reason]").fill(REASON)
    form.get_by_role("button", name="Save onboarding decision", exact=True).click()
    page.locator("#notice").filter(has_text="Onboarding decision saved.").wait_for()
    summary = admin.locator("summary").filter(has_text="DEMO · pending")
    summary.click()
    review = admin.locator("form").filter(
        has=page.get_by_role("heading", name="Independent pilot decision", exact=True)
    )
    review.locator("[name=decision]").select_option("expand")
    review.locator("[name=reason]").fill(REASON)
    review.get_by_role("button", name="Save pilot decision", exact=True).click()
    page.locator("#notice.error").filter(
        has_text="Demo activity cannot establish live pilot acceptance."
    ).wait_for()
    review.locator("[name=decision]").select_option("continue_pilot")
    review.get_by_role("button", name="Save pilot decision", exact=True).click()
    page.locator("#notice").filter(has_text="Pilot decision recorded.").wait_for()
    admin.locator("summary").filter(has_text="DEMO · continue pilot").wait_for()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert not errors
    page.locator("#logout").click()
    page.locator("#authentication").wait_for(state="visible")
    assert page.locator("#pilot-admin").inner_text() == ""
    login(page, live_url, catalog["staff"].email)
    page.locator("#staff-tab").click()
    assert page.locator("#pilot-staff").is_hidden()
    page.close()


def test_admin_creates_unapproved_institution_in_workspace(browser, live_url, pilot):
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    login(page, live_url, pilot["admin_user"].email)
    page.locator("#admin-tab").click()
    page.get_by_text("Add an institution", exact=True).click()
    form = page.locator("#create-institution-form")
    form.locator("[name=name]").fill("Browser Onboarding College")
    form.locator("[name=code]").fill("BROWSER-NEW")
    form.get_by_role("button", name="Create unapproved institution", exact=True).click()
    page.locator("#notice").filter(
        has_text="Institution created unapproved."
    ).wait_for()
    page.locator("#pilot-admin").get_by_text("Onboarding: draft", exact=True).wait_for()
    assert "Awaiting approval" in page.locator("#approval-status").inner_text()
    page.locator("#approval-form [name=approved]").check()
    page.locator("#approval-form [name=reason]").fill(REASON)
    page.locator("#approval-form button").click()
    page.locator("#notice.error").filter(
        has_text="Complete the onboarding review"
    ).wait_for()
    page.close()
