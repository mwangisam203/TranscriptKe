"""Account transitions and responsive workspace navigation use the actual API."""

# ruff: noqa: F811
import os

import pytest

from tests.browser.test_workspace import browser, live_url, login  # noqa: F401
from tests.conftest import PASSWORD
from tests.test_academic_records import create
from tests.test_orders import submit, workflow  # noqa: F401

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_BROWSER_TESTS") != "1", reason="Opt-in browser checks"
)


@pytest.mark.parametrize("width", [1440, 390])
def test_registration_verification_login_and_logout(browser, live_url, mailer, width):
    page = browser.new_page(viewport={"width": width, "height": 1000})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(live_url + "/workspace")
    if width == 1440:
        page.screenshot(path="/tmp/transcriptske-ui-auth.png", full_page=True)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.locator("#auth-login-tab").focus()
    page.keyboard.press("ArrowRight")
    assert page.locator("#auth-register-tab").get_attribute("aria-selected") == "true"
    page.locator("#register-form [name=full_name]").fill("Jane Student")
    page.locator("#register-form [name=email]").fill("ui-student@example.com")
    page.locator("#register-form [name=password]").fill(PASSWORD)
    page.locator("#register-form button").click()
    page.locator("#auth-verify").wait_for(state="visible")
    assert (
        page.locator("#resend-form [name=email]").input_value()
        == "ui-student@example.com"
    )
    page.locator("#verify-form [name=token]").fill(mailer.token("email_verification"))
    page.locator("#verify-form button").click()
    page.locator("#auth-login").wait_for(state="visible")
    assert (
        page.locator("#login-form [name=email]").input_value()
        == "ui-student@example.com"
    )
    page.locator("#login-form [name=password]").fill(PASSWORD)
    page.locator("#login-form button").click()
    page.locator("#notice").filter(has_text="Signed in.").wait_for()
    assert page.locator("#summary-linked").inner_text() == "0"
    assert page.locator("#summary-confirmed").inner_text() == "0"
    assert "Jane" in page.locator("#welcome").inner_text()
    assert page.locator("#my-links .empty-state").is_visible()
    assert (
        page.locator("#staff-tab").is_hidden()
        and page.locator("#admin-tab").is_hidden()
    )
    page.locator(".workspace-banner [data-view=orders-view]").click()
    assert page.locator("#orders-view").is_visible()
    assert page.locator("#view-eyebrow").inner_text() == "DOCUMENT REQUESTS"
    page.locator("nav [data-view=student-view]").click()
    if width == 1440:
        page.locator("#open-workspace-guide").click()
        assert page.locator("#workspace-guide").is_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    if page.locator("#logout").is_hidden():
        page.locator("#account-menu-toggle").click()
    page.locator("#logout").click()
    page.locator("#authentication").wait_for(state="visible")
    assert page.locator("#auth-login").is_visible()
    assert page.locator("#account-name").inner_text() == ""
    assert page.locator("#summary-linked").inner_text() == "—"
    assert not errors
    page.close()


def test_password_reset_returns_to_sign_in(browser, live_url, mailer, user_factory):
    user = user_factory("ui-recovery@example.com")
    page = browser.new_page()
    page.goto(live_url + "/workspace")
    page.get_by_role("button", name="Forgot password?", exact=True).click()
    page.locator("#reset-request-form [name=email]").fill(user.email)
    page.locator("#reset-request-form button").click()
    page.locator("#reset-form").wait_for(state="visible")
    page.locator("#reset-form [name=token]").fill(mailer.token("password_reset"))
    page.locator("#reset-form [name=password]").fill("new password 12345")
    page.locator("#reset-form button").click()
    page.locator("#auth-login").wait_for(state="visible")
    page.locator("#login-form [name=email]").fill(user.email)
    page.locator("#login-form [name=password]").fill("new password 12345")
    page.locator("#login-form button").click()
    page.locator("#notice").filter(has_text="Signed in.").wait_for()
    page.close()


@pytest.mark.parametrize("width", [1440, 390])
def test_workspace_overview_uses_records_and_keeps_role_navigation(
    browser, live_url, workflow, catalog, client, auth_headers, width
):
    create(
        client, catalog, workflow["user"], auth_headers, admission_number="UI/SECOND"
    )
    page = browser.new_page(viewport={"width": width, "height": 1000})
    login(page, live_url, workflow["user"].email)
    assert page.locator("#summary-linked").inner_text() == "2"
    assert page.locator("#summary-confirmed").inner_text() == "1"
    assert page.locator("#summary-pending").inner_text() == "1"
    if width == 1440:
        page.screenshot(path="/tmp/transcriptske-ui-workspace.png", full_page=True)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    if page.locator("#logout").is_hidden():
        page.locator("#account-menu-toggle").click()
    page.locator("#logout").click()
    page.locator("#authentication").wait_for(state="visible")
    login(page, live_url, catalog["manager"].email)
    page.locator("#staff-tab").click()
    page.locator("#operations-panel").wait_for(state="visible")
    assert page.locator("#account-role").inner_text() == "Institution manager"
    assert page.locator("#view-eyebrow").inner_text() == "INSTITUTION WORKSPACE"
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    if width == 1440:
        page.screenshot(path="/tmp/transcriptske-ui-institution.png", full_page=True)
    page.close()


def test_invalid_recipient_link_disables_delivery_controls(browser, live_url):
    page = browser.new_page(viewport={"width": 390, "height": 900})
    page.goto(live_url + "/recipient")
    assert (
        page.locator("#recipient-notice").inner_text()
        == "Open the complete delivery link from your notification email."
    )
    assert page.locator("#request-code button").is_disabled()
    assert page.locator("#download-document button").is_disabled()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path="/tmp/transcriptske-ui-recipient-mobile.png", full_page=True)
    page.close()


@pytest.mark.parametrize("width", [1440, 390])
def test_public_overview_is_scrollable_and_account_navigation_works(
    browser, live_url, user_factory, width
):
    user = user_factory("public-visitor@example.com")
    page = browser.new_page(viewport={"width": width, "height": 900})
    api_requests = []
    page.on(
        "request",
        lambda request: (
            api_requests.append(request.url) if "/api/v1/" in request.url else None
        ),
    )
    page.goto(live_url + "/workspace")
    assert page.locator("#public-information").is_visible()
    assert page.evaluate("document.documentElement.scrollHeight > innerHeight")
    page.locator("#public-navigation a[href='#about-workflow']").click()
    assert page.url.endswith("#about-workflow")
    page.locator("#public-navigation a[href='#about-documents']").click()
    assert page.url.endswith("#about-documents")
    assert (
        page.locator("#about-documents")
        .get_by_role("heading", name="Academic transcripts", exact=True)
        .is_visible()
    )
    page.locator("#public-navigation a[href='#about-questions']").click()
    page.locator(".public-faq summary").filter(
        has_text="Can I request documents without an account?"
    ).click()
    assert (
        page.locator(".public-faq details[open]")
        .inner_text()
        .find("create an account and verify your email")
        >= 0
    )
    assert all(url.endswith("/auth/session") for url in api_requests)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=f"/tmp/transcriptske-public-{width}.png", full_page=True)
    page.get_by_role("link", name="Create your account", exact=True).click()
    assert page.locator("#auth-register").is_visible()
    page.locator("#public-navigation .public-sign-in").click()
    assert page.locator("#auth-login").is_visible()
    login(page, live_url, user.email)
    assert page.locator("#public-information").is_hidden()
    assert page.locator("#public-navigation").is_hidden()
    if page.locator("#logout").is_hidden():
        page.locator("#account-menu-toggle").click()
    page.locator("#logout").click()
    page.locator("#authentication").wait_for(state="visible")
    assert page.locator("#public-information").is_visible()
    assert page.locator("#public-navigation").is_visible()
    page.close()


@pytest.mark.parametrize("width", [1440, 390])
def test_theme_follows_system_and_persists_across_pages(
    browser, live_url, user_factory, width
):
    page = browser.new_page(
        viewport={"width": width, "height": 900}, color_scheme="dark"
    )
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(live_url + "/workspace")
    assert page.locator("html").get_attribute("data-theme") == "dark"
    assert page.locator("#theme-preference").get_attribute("aria-pressed") == "true"
    page.locator("#theme-preference").click()
    assert page.locator("html").get_attribute("data-theme") == "light"
    page.reload()
    assert page.locator("html").get_attribute("data-theme") == "light"
    page.locator("#theme-preference").click()
    assert (
        page.evaluate(
            "getComputedStyle(document.querySelector('.panel')).backgroundColor"
        )
        == "rgb(23, 23, 25)"
    )
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=f"/tmp/transcriptske-dark-{width}.png", full_page=True)
    user = user_factory("dark-student@example.com")
    login(page, live_url, user.email)
    assert page.locator("html").get_attribute("data-theme") == "dark"
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(
        path=f"/tmp/transcriptske-dark-workspace-{width}.png", full_page=True
    )
    if page.locator("#logout").is_hidden():
        page.locator("#account-menu-toggle").click()
    page.locator("#logout").click()
    page.locator("#authentication").wait_for(state="visible")
    assert page.locator("#theme-preference").get_attribute("aria-pressed") == "true"
    page.goto(live_url + "/recipient")
    assert page.locator("html").get_attribute("data-theme") == "dark"
    assert page.locator("#theme-preference").get_attribute("aria-pressed") == "true"
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.locator("#theme-preference").click()
    page.emulate_media(color_scheme="light")
    page.locator("html[data-theme=light]").wait_for()
    page.emulate_media(color_scheme="dark")
    assert page.locator("html").get_attribute("data-theme") == "light"
    assert not errors
    page.close()


@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_footer_links_and_actions_use_supported_workflows(
    browser, live_url, user_factory, width, theme
):
    user = user_factory("footer-student@example.com")
    page = browser.new_page(viewport={"width": width, "height": 900})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(live_url + "/workspace")
    if page.locator("html").get_attribute("data-theme") != theme:
        page.locator("#theme-preference").click()
    footer = page.locator(".expanded-footer")
    assert page.locator("#footer-directory").is_visible()
    for link in page.locator("#footer-directory a").all():
        target = link.get_attribute("href")
        assert target.startswith("#")
        assert page.locator(target).count() == 1
    page.locator("#footer-directory").get_by_role(
        "link", name="Account and order help", exact=True
    ).click()
    assert page.url.endswith("#about-help")
    assert page.locator("#help-heading").is_visible()
    assert page.locator("#social-links a").count() == 4
    assert (
        "not registered TranscriptsKE accounts"
        in page.locator("#social-demo-note").inner_text()
    )
    for link in page.locator("#social-links a").all():
        assert "demo handle" in link.get_attribute("aria-label")
        assert link.get_attribute("href").startswith("https://")
        assert link.get_attribute("target") == "_blank"
        assert link.get_attribute("rel") == "noopener noreferrer"
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    footer.screenshot(path=f"/tmp/transcriptske-footer-{theme}-{width}.png")
    page.locator(".footer-quick-actions").get_by_role(
        "button", name="Check order status", exact=True
    ).click()
    assert page.locator("#auth-login").is_visible()
    assert page.locator("#workspace").is_hidden()
    assert (
        "Sign in to check your own order status" in page.locator("#notice").inner_text()
    )
    page.locator("#login-form [name=email]").fill(user.email)
    page.locator("#login-form [name=password]").fill(PASSWORD)
    page.locator("#login-form button").click()
    page.locator("#notice").filter(has_text="Signed in.").wait_for()
    assert page.locator("#orders-view").is_visible()
    assert page.locator("#footer-directory").is_hidden()
    assert page.locator(".footer-help-link").is_hidden()
    page.locator("nav [data-view=student-view]").click()
    page.locator(".footer-quick-actions").get_by_role(
        "button", name="Check order status", exact=True
    ).click()
    assert page.locator("#orders-view").is_visible()
    assert page.locator("html").get_attribute("data-theme") == theme
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    if page.locator("#logout").is_hidden():
        page.locator("#account-menu-toggle").click()
    page.locator("#logout").click()
    page.locator("#authentication").wait_for(state="visible")
    assert page.locator("#footer-directory").is_visible()
    assert not errors
    page.close()


@pytest.mark.parametrize("width", [1440, 390])
def test_home_keeps_session_and_account_can_change_password(
    browser, live_url, user_factory, width
):
    user = user_factory("settings-student@example.com")
    page = browser.new_page(viewport={"width": width, "height": 900})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    login(page, live_url, user.email)
    navigations = []
    page.on("framenavigated", lambda frame: navigations.append(frame.url))
    page.locator("nav [data-view=orders-view]").click()
    page.locator(".site-header .brand").click()
    assert page.locator("#student-view").is_visible()
    assert page.locator("#authentication").is_hidden()
    assert not navigations
    page.locator("nav [data-view=account-view]").click()
    assert page.locator("#settings-email").inner_text() == user.email
    form = page.locator("#change-password-form")
    form.locator("[name=current_password]").fill(PASSWORD)
    form.locator("[name=password]").fill("new account password 123")
    form.locator("[name=confirm_password]").fill("mismatched password 123")
    form.locator("button").click()
    page.locator("#notice").filter(has_text="new passwords do not match").wait_for()
    assert page.locator("#workspace").is_visible()
    form.locator("[name=confirm_password]").fill("new account password 123")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=f"/tmp/transcriptske-account-{width}.png", full_page=False)
    form.locator("button").click()
    page.locator("#authentication").wait_for(state="visible")
    page.locator("#notice").filter(has_text="Password changed").wait_for()
    page.locator("#login-form [name=password]").fill("new account password 123")
    page.locator("#login-form button").click()
    page.locator("#notice").filter(has_text="Signed in.").wait_for()
    page.locator("nav [data-view=account-view]").click()
    page.locator(".expanded-footer .brand").click()
    assert page.locator("#student-view").is_visible() and not navigations
    page.locator("nav [data-view=account-view]").click()
    page.locator("#account-password-recovery").click()
    page.locator("#auth-recover").wait_for(state="visible")
    assert page.locator("#reset-request-form [name=email]").input_value() == user.email
    assert not errors
    page.close()


@pytest.mark.parametrize("width", [1440, 390])
def test_forgot_admission_number_uses_masked_id_and_scoped_staff_reveal(
    browser, live_url, catalog, user_factory, width
):
    student = user_factory("id-browser@example.com")
    page = browser.new_page(viewport={"width": width, "height": 900})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    login(page, live_url, student.email)
    page.locator("#student-institution").select_option(str(catalog["institution"].id))
    page.locator(f"#student-service option[value='{catalog['service'].id}']").wait_for(
        state="attached"
    )
    page.locator("#student-service").select_option(str(catalog["service"].id))
    page.locator("#record-lookup-method").select_option("identity")
    assert page.locator("#record-form [name=admission_number]").is_disabled()
    page.locator("#record-form [name=id_number]").fill("12345678")
    page.locator("#record-form [name=name_on_record]").fill("Jane ID Lookup")
    page.locator("#record-form [name=program]").fill("Computer Science")
    page.select_option("#record-currently-enrolled", "yes")
    page.locator("#record-form [name=attendance_start_date]").fill("2018-09")
    page.locator("#record-submit").click()
    page.locator("#my-links .badge.pending").wait_for()
    assert "••••78" in page.locator("#my-links").inner_text()
    assert "12345678" not in page.locator("body").inner_text()
    assert page.locator("#record-form [name=id_number]").input_value() == ""
    assert page.locator("#record-form [name=admission_number]").is_enabled()
    if page.locator("#logout").is_hidden():
        page.locator("#account-menu-toggle").click()
    page.locator("#logout").click()
    page.locator("#authentication").wait_for(state="visible")
    login(page, live_url, catalog["staff"].email)
    page.locator("#staff-tab").click()
    page.get_by_role("button", name="Open review", exact=True).click()
    page.locator("#review-area").wait_for(state="visible")
    assert "12345678" not in page.locator("body").inner_text()
    page.get_by_role(
        "button", name="View ID for institutional matching", exact=True
    ).click()
    page.locator("#review-details p").filter(
        has_text="ID/passport: 12345678"
    ).wait_for()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    if page.locator("#logout").is_hidden():
        page.locator("#account-menu-toggle").click()
    page.locator("#logout").click()
    page.locator("#authentication").wait_for(state="visible")
    assert "12345678" not in page.locator("body").inner_text()
    assert not errors
    page.close()


@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize("destination", ["self", "institution"])
def test_search_and_destination_draft(
    browser, live_url, workflow, catalog, width, destination
):
    page = browser.new_page(viewport={"width": width, "height": 1000})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    login(page, live_url, workflow["user"].email)
    page.locator("#student-institution-search").fill("no such institution")
    assert (
        page.locator("#student-institution-results").inner_text()
        == "0 institutions found"
    )
    page.locator("#student-institution-search").fill(catalog["institution"].code)
    assert page.locator("#student-institution option").count() == 2
    page.locator("nav [data-view=orders-view]").click()
    page.locator("#order-institution-search").fill("no such institution")
    assert page.locator("#create-order").is_disabled()
    page.locator("#order-institution-search").fill(catalog["institution"].code)
    page.locator("#order-institution-suggestions button").filter(
        has_text=catalog["institution"].name
    ).click()
    page.select_option("#new-order-destination", destination)
    if destination == "institution":
        assert page.locator("#new-order-self-email").is_disabled()
        page.locator("#new-order-institution-name").fill("Demo receiving university")
        page.locator("#new-order-institution-email").fill("registrar@example.com")
    else:
        assert (
            page.locator("#new-order-self-email").input_value()
            == workflow["user"].email
        )
        assert page.locator("#new-order-institution-email").is_disabled()
    if destination == "institution":
        page.locator("#new-order-form").screenshot(
            path=f"/tmp/transcriptske-request-documents-{width}.png"
        )
    page.locator("#create-order").click()
    page.locator("#notice").filter(
        has_text="Continue with document selection"
    ).wait_for()
    assert (
        page.locator("#order-recipients [name=recipient_destination]").input_value()
        == destination
    )
    expected = (
        "registrar@example.com"
        if destination == "institution"
        else workflow["user"].email
    )
    assert (
        page.locator("#order-recipients [name=recipient_email]").input_value()
        == expected
    )
    assert page.locator("#order-items fieldset").count() == 1
    page.locator("#order-editor [name=purpose]").fill("Application documents")
    page.locator("#quote-order").click()
    page.locator("#order-quote").wait_for(state="visible")
    assert expected in page.locator("#order-quote").inner_text()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert not errors
    page.close()


@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize("kind", ["national_id", "driving_licence"])
def test_required_identity_images_and_private_staff_download(
    browser, live_url, catalog, user_factory, db, width, kind
):
    from tests.test_identity_images import photograph

    catalog["policy"].required_fields = [
        "program",
        "attendance_start_year",
        "identity_images",
    ]
    db.commit()
    student = user_factory("proof-browser@example.com")
    page = browser.new_page(viewport={"width": width, "height": 1000})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    login(page, live_url, student.email)
    page.select_option("#student-institution", str(catalog["institution"].id))
    page.locator(f"#student-service option[value='{catalog['service'].id}']").wait_for(
        state="attached"
    )
    page.select_option("#student-service", str(catalog["service"].id))
    assert page.locator("#record-include-images").is_checked()
    assert page.locator("#record-include-images").is_disabled()
    page.locator("#record-form [name=admission_number]").fill("PROOF/001")
    page.locator("#record-form [name=name_on_record]").fill("Jane Proof")
    page.locator("#record-form [name=program]").fill("Computer Science")
    page.select_option("#record-currently-enrolled", "yes")
    page.locator("#record-form [name=attendance_start_date]").fill("2018-09")
    page.select_option("#record-identity-document-type", kind)
    page.locator("#record-identity-front").set_input_files(
        {"name": "front.png", "mimeType": "image/png", "buffer": photograph()}
    )
    page.locator("#record-submit").click()
    assert page.locator("#my-links .badge.pending").count() == 0
    page.locator("#record-identity-back").set_input_files(
        {"name": "back.png", "mimeType": "image/png", "buffer": photograph("red")}
    )
    page.locator("#record-image-fields").screenshot(
        path=f"/tmp/transcriptske-id-images-{width}.png"
    )
    page.locator("#record-submit").click()
    page.locator("#my-links .badge.pending").wait_for()
    assert page.locator("#record-identity-front").input_value() == ""
    assert page.locator("#record-identity-back").input_value() == ""
    assert page.get_by_role("button", name="Download ID front", exact=True).is_visible()
    if page.locator("#logout").is_hidden():
        page.locator("#account-menu-toggle").click()
    page.locator("#logout").click()
    page.locator("#authentication").wait_for(state="visible")
    login(page, live_url, catalog["staff"].email)
    page.locator("#staff-tab").click()
    page.get_by_role("button", name="Open review", exact=True).click()
    page.locator("#review-area").wait_for(state="visible")
    with page.expect_download() as download_info:
        page.locator("#review-details").get_by_role(
            "button", name="Download ID front", exact=True
        ).click()
    assert download_info.value.suggested_filename == f"{kind}-front.jpg"
    assert download_info.value.failure() is None
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert not errors
    page.close()


def test_changing_institution_clears_selected_identity_photographs(
    browser, live_url, catalog, user_factory, institutions
):
    from tests.test_identity_images import photograph

    page = browser.new_page(viewport={"width": 390, "height": 1000})
    student = user_factory("proof-switch@example.com")
    login(page, live_url, student.email)
    page.select_option("#student-institution", str(catalog["institution"].id))
    page.locator(f"#student-service option[value='{catalog['service'].id}']").wait_for(
        state="attached"
    )
    page.select_option("#student-service", str(catalog["service"].id))
    page.locator("#record-include-images").check()
    page.locator("#record-identity-front").set_input_files(
        {"name": "private-front.png", "mimeType": "image/png", "buffer": photograph()}
    )
    # Filtering the current institution preserves the selected service and image.
    page.locator("#student-institution-search").fill(catalog["institution"].code)
    assert page.locator("#student-service").input_value() == str(catalog["service"].id)
    assert "private-front.png" in page.locator("#record-identity-front").input_value()
    page.locator("#student-institution-search").fill("")
    page.select_option("#student-institution", str(institutions[1].id))
    assert page.locator("#record-identity-front").input_value() == ""
    assert not page.locator("#record-include-images").is_checked()
    assert page.locator("#record-identity-front").is_disabled()
    page.close()


@pytest.mark.parametrize("width", [1440, 390])
def test_institution_suggestions_and_month_year_attendance(
    browser, live_url, catalog, user_factory, db, width
):
    from sqlalchemy import select

    from app.models.academic import AcademicRecordLink

    catalog["institution"].name = "University of Bright Horizons"
    db.commit()
    student = user_factory("bright-browser@example.com")
    page = browser.new_page(viewport={"width": width, "height": 1000})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    login(page, live_url, student.email)
    page.locator("#student-institution-search").fill("bri uni")
    suggestion = page.locator("#student-institution-suggestions button").filter(
        has_text="University of Bright Horizons"
    )
    assert suggestion.is_visible()
    if width == 1440:
        page.locator("#student-institution-search").press("ArrowDown")
        page.keyboard.press("Enter")
    else:
        suggestion.click()
    page.locator(f"#student-service option[value='{catalog['service'].id}']").wait_for(
        state="attached"
    )
    page.select_option("#student-service", str(catalog["service"].id))
    page.locator("#record-form [name=admission_number]").fill("BRIGHT/001")
    page.locator("#record-form [name=name_on_record]").fill("Jane Bright")
    page.locator("#record-form [name=program]").fill("Computer Science")
    page.select_option("#record-currently-enrolled", "yes")
    page.locator("#record-form [name=attendance_start_date]").fill("2020-09")
    page.select_option("#record-currently-enrolled", "no")
    page.locator("#record-form [name=attendance_end_date]").fill("2022-06")
    page.locator("#record-submit").click()
    page.locator("#my-links .badge.pending").wait_for()
    assert "Sep 2020" in page.locator("#my-links").inner_text()
    assert "Jun 2022" in page.locator("#my-links").inner_text()
    link = db.scalar(
        select(AcademicRecordLink).where(AcademicRecordLink.user_id == student.id)
    )
    assert link.attendance_start_month == 9 and link.attendance_end_month == 6
    if page.locator("#logout").is_hidden():
        page.locator("#account-menu-toggle").click()
    page.locator("#logout").click()
    page.locator("#authentication").wait_for(state="visible")
    login(page, live_url, catalog["staff"].email)
    page.locator("#staff-tab").click()
    page.get_by_role("button", name="Open review", exact=True).click()
    page.locator("#review-area").wait_for(state="visible")
    assert "Sep 2020" in page.locator("#review-details").inner_text()
    assert "Jun 2022" in page.locator("#review-details").inner_text()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert not errors
    page.close()


@pytest.mark.parametrize("width", [1440, 390])
def test_directory_can_be_browsed_before_and_after_login(
    browser, live_url, user_factory, db, monkeypatch, width
):
    from app.cli import seed_academic_demo
    from app.core.config import settings

    monkeypatch.setattr(settings, "APP_ENV", "development")
    seed_academic_demo(db)
    student = user_factory("directory-browser@example.com")
    page = browser.new_page(viewport={"width": width, "height": 1000})
    page.goto(live_url + "/workspace")
    page.locator("#public-navigation [data-institution-directory]").click()
    page.locator("#directory-status").filter(
        has_text="10 institutions found"
    ).wait_for()
    assert page.locator("#directory-list article").count() == 10
    page.locator("#directory-search").fill("lake uni")
    assert page.locator("#directory-list article").count() == 1
    assert "Lakeview" in page.locator("#directory-list").inner_text()
    page.locator("#directory-search").fill("does not exist")
    assert "0 institutions found" in page.locator("#directory-status").inner_text()
    page.locator("#directory-search").fill("")
    page.locator("#institution-directory").screenshot(
        path=f"/tmp/transcriptske-directory-{width}.png"
    )
    page.locator("#close-institution-directory").click()
    login(page, live_url, student.email)
    page.locator(".sidebar [data-institution-directory]").click()
    page.locator("#directory-status").filter(
        has_text="10 institutions found"
    ).wait_for()
    assert page.locator("#account-menu-toggle").is_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.close()


@pytest.mark.parametrize("width", [1440, 390])
def test_guest_order_signup_personal_profile_and_enrollment(
    browser, live_url, catalog, mailer, db, width
):
    from sqlalchemy import select

    from app.models.academic import AcademicRecordLink
    from app.models.profile import UserProfile
    from tests.test_profiles import DETAILS

    page = browser.new_page(viewport={"width": width, "height": 1000})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(live_url + "/workspace")
    page.locator("#start-document-request").click()
    assert page.locator("#guided-registration-details").is_visible()
    assert page.locator("#register-form [name=full_name]").is_disabled()
    form = page.locator("#register-form")
    form.locator("[name=email]").fill("guided-browser@example.com")
    for name, value in DETAILS.items():
        if name == "highest_education":
            form.locator(f"[name={name}]").select_option(value)
        else:
            form.locator(f"[name={name}]").fill(value)
    form.locator("[name=password]").fill(PASSWORD)
    form.locator("[name=confirm_password]").fill("a different password")
    form.locator("button").click()
    page.locator("#notice").filter(has_text="passwords do not match").wait_for()
    assert db.scalar(select(UserProfile)) is None
    form.locator("[name=confirm_password]").fill(PASSWORD)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.locator(".auth-card").screenshot(
        path=f"/tmp/transcriptske-guided-signup-{width}.png"
    )
    form.locator("button").click()
    page.locator("#auth-verify").wait_for(state="visible")
    stored = db.scalar(select(UserProfile))
    assert "1998-02-13" not in stored.details_ciphertext
    page.locator("#verify-form [name=token]").fill(mailer.token("email_verification"))
    page.locator("#verify-form button").click()
    page.locator("#auth-login").wait_for(state="visible")
    page.locator("#login-form [name=password]").fill(PASSWORD)
    page.locator("#login-form button").click()
    page.locator("#notice").filter(has_text="Signed in.").wait_for()
    assert page.locator("#student-view").is_visible()
    assert (
        "Jane Dosy Student" in page.locator("#enrollment-personal-summary").inner_text()
    )
    assert "1998-02-13" in page.locator("#enrollment-personal-summary").inner_text()
    assert (
        page.locator("#record-form [name=name_on_record]").input_value()
        == "Jane Dosy Student"
    )
    page.select_option("#student-institution", str(catalog["institution"].id))
    page.locator(f"#student-service option[value='{catalog['service'].id}']").wait_for(
        state="attached"
    )
    page.select_option("#student-service", str(catalog["service"].id))
    page.select_option("#record-currently-enrolled", "no")
    page.locator("#record-form [name=admission_number]").fill("GUIDED/001")
    page.locator("#record-form [name=program]").fill("Computer Science")
    page.locator("#record-form [name=attendance_start_date]").fill("2016-09")
    page.locator("#record-form [name=attendance_end_date]").fill("2020-06")
    page.locator("#record-form [name=previous_names]").fill("Jane Former")
    page.locator("#record-submit").click()
    page.locator("#my-links .badge.pending").wait_for()
    link = db.scalar(select(AcademicRecordLink))
    assert link.currently_enrolled is False and link.attendance_end_month == 6
    assert link.previous_names == ["Jane Former"]
    page.locator("nav [data-view=account-view]").click()
    assert (
        page.locator("#personal-profile-form [name=date_of_birth]").input_value()
        == "1998-02-13"
    )
    page.locator("#personal-profile-form [name=mobile_phone]").fill("+254700000001")
    page.locator("#personal-profile-form button").click()
    page.locator("#notice").filter(
        has_text="personal details have been updated"
    ).wait_for()
    if page.locator("#logout").is_hidden():
        page.locator("#account-menu-toggle").click()
    page.locator("#logout").click()
    page.locator("#authentication").wait_for(state="visible")
    assert (
        page.locator("#personal-profile-form [name=date_of_birth]").input_value() == ""
    )
    login(page, live_url, catalog["staff"].email)
    page.locator("#staff-tab").click()
    page.get_by_role("button", name="Open review", exact=True).click()
    page.locator("#review-area").wait_for(state="visible")
    page.get_by_role(
        "button", name="View account name and birth date", exact=True
    ).click()
    page.locator("#review-details").filter(has_text="1998-02-13").wait_for()
    assert "+254700000001" not in page.locator("#review-details").inner_text()
    assert not errors
    page.close()


@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_account_dropdown_identity_navigation_support_and_logout(
    browser, live_url, user_factory, width, theme
):
    user = user_factory("menu-user@example.com")
    page = browser.new_page(viewport={"width": width, "height": 950})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(live_url + "/workspace")
    assert page.locator("#header-account").is_hidden()
    login(page, live_url, user.email)
    if page.locator("html").get_attribute("data-theme") != theme:
        page.locator("#theme-preference").click()
    toggle = page.locator("#account-menu-toggle")
    assert toggle.is_visible() and toggle.get_attribute("aria-expanded") == "false"
    assert page.locator("#header-account-name").inner_text() == user.full_name
    if theme == "dark":
        assert (
            toggle.evaluate("el => getComputedStyle(el).color") == "rgb(244, 244, 245)"
        )
    page.locator("#header-account-name").evaluate(
        "el => el.textContent = 'A very long account name with several family names'"
    )
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.locator("#header-account-name").evaluate(
        "(el, name) => el.textContent = name", user.full_name
    )
    toggle.focus()
    page.keyboard.press("ArrowDown")
    assert toggle.get_attribute("aria-expanded") == "true"
    assert page.locator("#header-account-email").inner_text() == user.email
    assert page.locator("#header-account-role").inner_text() == "Student account"
    assert page.locator("#account-menu-settings").evaluate(
        "el => el === document.activeElement"
    )
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=f"/tmp/transcriptske-account-menu-{theme}-{width}.png")
    page.keyboard.press("Escape")
    assert page.locator("#account-menu-panel").is_hidden()
    assert toggle.evaluate("el => el === document.activeElement")
    toggle.click()
    page.locator(".site-header .brand").click()
    assert page.locator("#account-menu-panel").is_hidden()
    toggle.click()
    page.locator("#account-menu-settings").click()
    assert page.locator("#account-view").is_visible()
    assert page.locator("#settings-email").inner_text() == user.email
    toggle.click()
    page.locator("#account-menu-support").click()
    dialog = page.locator("#account-support-dialog")
    assert dialog.is_visible()
    assert "review, sign and pay" in dialog.inner_text()
    dialog.locator("[data-support-view=orders-view]").click()
    assert dialog.is_hidden() and page.locator("#orders-view").is_visible()
    toggle.click()
    page.locator("#account-menu-support").click()
    page.keyboard.press("Escape")
    assert dialog.is_hidden()
    assert toggle.evaluate("el => el === document.activeElement")
    toggle.click()
    page.locator("#logout").click()
    page.locator("#authentication").wait_for(state="visible")
    assert page.locator("#header-account").is_hidden()
    assert page.locator("#header-account-email").inner_text() == ""
    assert page.locator("#header-account-name").inner_text() == ""
    assert not errors
    page.close()


@pytest.mark.parametrize("width", [1440, 390])
def test_notification_updates_navigation_read_state_and_header(
    browser, live_url, workflow, width
):
    page = browser.new_page(viewport={"width": width, "height": 950})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(live_url + "/workspace")
    assert page.locator("#header-notifications").is_hidden()
    login(page, live_url, workflow["user"].email)
    page.locator("#notification-count").wait_for(state="visible")
    page.locator("#notification-toggle").click()
    item = page.locator("#notification-list button").filter(
        has_text="Enrollment confirmed"
    )
    item.wait_for()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    item.click()
    page.locator("#record-form-title").filter(
        has_text="Your enrollment is confirmed"
    ).wait_for()
    assert page.locator("#notification-panel").is_hidden()
    page.locator("#notification-toggle").click()
    page.locator("#notification-status").filter(
        has_text="Showing the latest"
    ).wait_for()
    assert page.locator("#notification-list .is-read").count() == 1
    assert page.locator("#notification-count").is_hidden()
    page.keyboard.press("Escape")
    assert page.locator("#notification-panel").is_hidden()
    page.reload()
    page.locator("#header-notifications").wait_for(state="visible")
    page.locator("#notification-toggle").click()
    page.locator("#notification-status").filter(
        has_text="Showing the latest"
    ).wait_for()
    assert page.locator("#notification-list .is-read").count() == 1
    page.locator("#account-menu-toggle").click()
    assert page.locator("#notification-panel").is_hidden()
    page.locator("#account-menu-toggle").click()
    page.locator("[data-header-view=orders-view]").click()
    assert page.locator("#orders-view").is_visible()
    if page.locator("html").get_attribute("data-theme") == "dark":
        page.locator("#theme-preference").click()
    page.locator("#theme-preference").click()
    assert page.locator("html").get_attribute("data-theme") == "dark"
    assert page.locator("html").evaluate("el => el.classList.contains('theme-beam')")
    page.wait_for_timeout(1000)
    page.locator("#notification-toggle").click()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=f"/tmp/transcriptske-header-notifications-{width}.png")
    page.locator("#account-menu-toggle").click()
    page.locator("#logout").click()
    page.locator("#notice").filter(has_text="Signed out.").wait_for()
    assert page.locator("#notification-list").inner_text() == ""
    assert page.locator("#header-notifications").is_hidden()
    assert page.locator("#member-navigation").is_hidden()
    assert not errors
    page.close()


def test_notifications_show_new_registrar_question_and_recover_from_error(
    browser, live_url, workflow, client
):
    page = browser.new_page(viewport={"width": 1440, "height": 950})
    login(page, live_url, workflow["user"].email)
    page.locator("#notification-count").wait_for(state="visible")
    page.locator("#notification-toggle").click()
    page.locator("#notification-status").filter(
        has_text="Showing the latest"
    ).wait_for()
    page.locator("#notification-read").click()
    assert page.locator("#notification-count").is_hidden()
    submit(client, workflow)
    response = client.post(
        workflow["staff_url"] + "/messages",
        headers=workflow["staff_headers"],
        json={"body": "Which graduation session?", "requires_response": True},
    )
    assert response.status_code == 201
    page.locator("#notification-refresh").click()
    page.locator("#notification-list button").filter(
        has_text="Your institution needs a reply"
    ).wait_for()
    assert page.locator("#notification-count").inner_text() == "1"
    page.locator("#notification-list button").filter(
        has_text="Your institution needs a reply"
    ).click()
    page.locator("#order-conversation").filter(
        has_text="Which graduation session?"
    ).wait_for()
    page.locator("#notification-toggle").click()
    page.locator("#notification-status").filter(
        has_text="Showing the latest"
    ).wait_for()
    assert page.locator("#notification-count").is_hidden()
    page.route(
        "**/api/v1/orders?offset=0&limit=100",
        lambda route: route.fulfill(status=500, body="Internal Server Error"),
    )
    page.locator("#notification-refresh").click()
    page.locator("#notification-status").filter(
        has_text="Unable to check updates"
    ).wait_for()
    page.unroute("**/api/v1/orders?offset=0&limit=100")
    page.locator("#notification-refresh").click()
    page.locator("#notification-status").filter(
        has_text="Showing the latest"
    ).wait_for()
    page.close()


@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_account_menu_fits_short_viewport_and_keeps_signout_visible(
    browser, live_url, user_factory, width, theme
):
    user = user_factory("viewport-menu@example.com")
    page = browser.new_page(viewport={"width": width, "height": 420})
    login(page, live_url, user.email)
    if page.locator("html").get_attribute("data-theme") != theme:
        page.locator("#theme-preference").click()
    page.locator("#account-menu-toggle").click()
    page.set_viewport_size({"width": width, "height": 300})
    page.wait_for_timeout(100)
    menu = page.locator("#account-menu-panel")
    assert menu.evaluate(
        "el => {const r = el.getBoundingClientRect(); return r.top >= 0 && r.bottom <= innerHeight && r.left >= 0 && r.right <= innerWidth;}"
    )
    assert page.locator("#logout").evaluate(
        "el => {const r = el.getBoundingClientRect(); return r.height > 0 && r.top >= 0 && r.bottom <= innerHeight && el.contains(document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2));}"
    )
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=f"/tmp/transcriptske-account-viewport-{theme}-{width}.png")
    page.locator("#logout").click()
    page.locator("#notice").filter(has_text="Signed out.").wait_for()
    assert menu.is_hidden()
    page.close()


@pytest.mark.parametrize("width", [1440, 390])
def test_typing_search_and_autosave_do_not_move_cards(
    browser, live_url, catalog, user_factory, width
):
    page = browser.new_page(viewport={"width": width, "height": 1000})
    login(page, live_url, user_factory().email)
    page.locator("#student-institution").select_option(str(catalog["institution"].id))
    page.locator("#student-service option").nth(1).wait_for(state="attached")
    page.locator("#record-form [name=name_on_record]").fill("Jane")
    page.locator("#enrollment-draft-state").filter(
        has_text="Draft saved automatically."
    ).wait_for()

    def position(selector):
        return page.locator(selector).evaluate(
            "element => { const rect = element.getBoundingClientRect(); return {x: rect.left + scrollX, y: rect.top + scrollY}; }"
        )

    baseline = position(".record-list-panel")
    search_baseline = position("#student-institution")
    page.locator("#student-institution-search").fill(catalog["institution"].name[:4])
    page.locator("#student-institution-suggestions").wait_for(state="visible")
    after_search = position("#student-institution")
    assert abs(after_search["y"] - search_baseline["y"]) < 1
    page.locator("#student-institution-search").press("Escape")
    page.locator("#record-form [name=name_on_record]").fill("Jane Student")
    page.locator("#enrollment-draft-state").filter(
        has_text="Draft saved automatically."
    ).wait_for()
    after_save = position(".record-list-panel")
    assert abs(after_save["y"] - baseline["y"]) < 1
    assert abs(after_save["x"] - baseline["x"]) < 1
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.close()
