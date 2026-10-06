"""Account transitions and responsive workspace navigation use the actual API."""

# ruff: noqa: F811
import os

import pytest

from tests.browser.test_workspace import browser, live_url, login  # noqa: F401
from tests.conftest import PASSWORD
from tests.test_academic_records import create
from tests.test_orders import workflow  # noqa: F401

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
    assert not api_requests
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=f"/tmp/transcriptske-public-{width}.png", full_page=True)
    page.get_by_role("link", name="Create your account", exact=True).click()
    assert page.locator("#auth-register").is_visible()
    page.locator("#public-navigation .public-sign-in").click()
    assert page.locator("#auth-login").is_visible()
    login(page, live_url, user.email)
    assert page.locator("#public-information").is_hidden()
    assert page.locator("#public-navigation").is_hidden()
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
    assert page.locator("#theme-preference").input_value() == "system"
    page.locator("#theme-preference").select_option("light")
    assert page.locator("html").get_attribute("data-theme") == "light"
    page.reload()
    assert page.locator("html").get_attribute("data-theme") == "light"
    page.locator("#theme-preference").select_option("dark")
    assert (
        page.evaluate(
            "getComputedStyle(document.querySelector('.panel')).backgroundColor"
        )
        == "rgb(25, 43, 41)"
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
    page.locator("#logout").click()
    page.locator("#authentication").wait_for(state="visible")
    assert page.locator("#theme-preference").input_value() == "dark"
    page.goto(live_url + "/recipient")
    assert page.locator("html").get_attribute("data-theme") == "dark"
    assert page.locator("#theme-preference").input_value() == "dark"
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.locator("#theme-preference").select_option("system")
    page.emulate_media(color_scheme="light")
    page.locator("html[data-theme=light]").wait_for()
    page.emulate_media(color_scheme="dark")
    page.locator("html[data-theme=dark]").wait_for()
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
    page.locator("#theme-preference").select_option(theme)
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
    assert "Sign in to request documents" in page.locator("#notice").inner_text()
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
    page.locator("#record-form [name=id_number]").fill("AB12345678")
    page.locator("#record-form [name=name_on_record]").fill("Jane ID Lookup")
    page.locator("#record-form [name=program]").fill("Computer Science")
    page.locator("#record-form [name=attendance_start_year]").fill("2018")
    page.locator("#record-submit").click()
    page.locator("#my-links .badge.pending").wait_for()
    assert "••••78" in page.locator("#my-links").inner_text()
    assert "AB12345678" not in page.locator("body").inner_text()
    assert page.locator("#record-form [name=id_number]").input_value() == ""
    assert page.locator("#record-form [name=admission_number]").is_enabled()
    page.locator("#logout").click()
    page.locator("#authentication").wait_for(state="visible")
    login(page, live_url, catalog["staff"].email)
    page.locator("#staff-tab").click()
    page.get_by_role("button", name="Open review", exact=True).click()
    page.locator("#review-area").wait_for(state="visible")
    assert "AB12345678" not in page.locator("body").inner_text()
    page.get_by_role(
        "button", name="View ID for institutional matching", exact=True
    ).click()
    page.locator("#review-details p").filter(
        has_text="ID/passport: AB12345678"
    ).wait_for()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.locator("#logout").click()
    page.locator("#authentication").wait_for(state="visible")
    assert "AB12345678" not in page.locator("body").inner_text()
    assert not errors
    page.close()
