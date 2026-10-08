"""Recovery is optional; checkout progresses without a separate save-draft step."""
# ruff: noqa: F811 -- shared pytest fixtures

import os

import pytest

from tests.browser.test_workspace import browser, live_url, login  # noqa: F401
from tests.test_identity_images import photograph
from tests.test_orders import checked, workflow  # noqa: F401

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_BROWSER_TESTS") != "1", reason="Opt-in browser checks"
)


def test_partial_personal_details_autosave_resume_and_delete(
    browser, live_url, user_factory
):
    page = browser.new_page()
    login(page, live_url, user_factory().email)
    page.locator("#checkout-profile-form [name=first_name]").fill("Unfinished")
    page.locator("#checkout-profile-draft-state").filter(
        has_text="Draft saved automatically"
    ).wait_for()
    page.reload()
    page.locator("#checkout-profile-draft-state").filter(
        has_text="Unfinished personal details restored."
    ).wait_for()
    assert (
        page.locator("#checkout-profile-form [name=first_name]").input_value()
        == "Unfinished"
    )
    page.locator("#discard-profile-draft").click()
    page.locator("#notice").filter(
        has_text="Unfinished personal details deleted"
    ).wait_for()
    page.reload()
    page.locator("#workspace").wait_for(state="visible")
    assert page.locator("#checkout-profile-form [name=first_name]").input_value() == ""
    page.close()


def test_same_file_uploads_show_error_before_submission(
    browser, live_url, catalog, user_factory
):
    page = browser.new_page()
    login(page, live_url, user_factory().email)
    page.locator("#student-institution").select_option(str(catalog["institution"].id))
    page.locator("#record-include-images").check()
    photo = photograph()
    page.locator("#record-identity-front").set_input_files(
        {"name": "front.png", "mimeType": "image/png", "buffer": photo}
    )
    page.locator("#record-identity-back").set_input_files(
        {"name": "renamed.png", "mimeType": "image/png", "buffer": photo}
    )
    page.locator("#identity-pair-error").wait_for(state="visible")
    page.locator("#record-identity-back").set_input_files(
        {"name": "back.png", "mimeType": "image/png", "buffer": photograph("red")}
    )
    page.locator("#identity-pair-error").wait_for(state="hidden")
    page.close()


def test_continue_review_saves_edits_and_draft_can_be_deleted(
    browser, live_url, workflow, client
):
    page = browser.new_page()
    login(page, live_url, workflow["user"].email)
    page.locator("nav [data-view=orders-view]").click()
    page.get_by_role("button", name="Open order", exact=True).click()
    page.locator("#order-editor [name=purpose]").fill("Continuous checkout")
    page.get_by_role(
        "button", name="Continue to review and consent", exact=True
    ).click()
    page.locator("#order-quote").wait_for(state="visible")
    assert "Continuous checkout" in page.locator("#order-quote").inner_text()
    assert (
        checked(client.get(workflow["url"], headers=workflow["headers"]))["purpose"]
        == "Continuous checkout"
    )
    page.locator("#discard-order").click()
    page.locator("#notice").filter(has_text="Unfinished checkout deleted").wait_for()
    assert client.get(workflow["url"], headers=workflow["headers"]).status_code == 404
    assert page.locator("#order-detail").is_hidden()
    page.close()
