import logging
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1.academic_records import router as academic_records_router
from app.api.v1.auth import router as auth_router
from app.api.v1.catalog import router as catalog_router
from app.api.v1.fulfillment import router as fulfillment_router
from app.api.v1.institutions import router as institutions_router
from app.api.v1.issuance import router as issuance_router
from app.api.v1.operations import router as operations_router
from app.api.v1.orders import router as orders_router
from app.api.v1.payments import router as payments_router
from app.api.v1.pilot import router as pilot_router
from app.api.v1.platform_finance import router as platform_finance_router
from app.api.v1.profiles import router as profiles_router
from app.api.v1.staff import router as staff_router
from app.api.v1.workspace_drafts import router as workspace_drafts_router
from app.core.config import settings
from app.db.session import get_db
from app.services.order_attachments import AttachmentBodyLimit
from app.services.payment_gateways import PaymentAccessLogFilter
from app.services.readiness import database_ready

logging.getLogger("uvicorn.access").addFilter(PaymentAccessLogFilter())

app = FastAPI(
    title=f"{settings.APP_NAME} API",
    description="Academic transcript request and verification platform for Kenya.",
    version="0.1.0",
)


@app.exception_handler(RequestValidationError)
async def safe_validation_errors(request: Request, exc: RequestValidationError):
    # Never echo submitted passwords or identity numbers in validation responses.
    return JSONResponse(
        status_code=422,
        content={
            "detail": [
                {"type": error["type"], "loc": error["loc"], "msg": error["msg"]}
                for error in exc.errors()
            ]
        },
    )


app.include_router(auth_router, prefix="/api/v1")
app.include_router(profiles_router, prefix="/api/v1")
app.include_router(workspace_drafts_router, prefix="/api/v1")
app.include_router(institutions_router, prefix="/api/v1")
app.include_router(staff_router, prefix="/api/v1")
app.include_router(catalog_router, prefix="/api/v1")
app.include_router(academic_records_router, prefix="/api/v1")
app.include_router(orders_router, prefix="/api/v1")
app.include_router(fulfillment_router, prefix="/api/v1")
app.include_router(payments_router, prefix="/api/v1")
app.include_router(platform_finance_router, prefix="/api/v1")
app.include_router(issuance_router, prefix="/api/v1")
app.include_router(operations_router, prefix="/api/v1")
app.include_router(pilot_router, prefix="/api/v1")
app.add_middleware(AttachmentBodyLimit)

STATIC_DIRECTORY = Path(__file__).resolve().parent / "static"
app.mount(
    "/workspace/assets",
    StaticFiles(directory=STATIC_DIRECTORY),
    name="workspace-assets",
)


@app.middleware("http")
async def private_api_responses(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith(
        (
            "/api/v1/me/",
            "/api/v1/staff/",
            "/api/v1/auth/",
            "/api/v1/admin/",
            "/api/v1/orders",
            "/api/v1/payments",
            "/api/v1/deliveries",
        )
    ):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(STATIC_DIRECTORY / "favicon.svg", media_type="image/svg+xml")


@app.get("/workspace", include_in_schema=False)
def workspace():
    mail_hint = ""
    if settings.APP_ENV == "development" and settings.MAIL_BACKEND == "file":
        mail_hint = (
            '<p class="development-mail-hint"><strong>Need your code?</strong><br>'
            "Email delivery is unavailable in this preview. Contact support for your verification or reset code.</p>"
        )
    content = (STATIC_DIRECTORY / "workspace.html").read_text(encoding="utf-8")
    return HTMLResponse(
        content.replace("<!-- development-mail-hint -->", mail_hint),
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'self'; script-src 'self' https://js.stripe.com https://*.js.stripe.com https://checkout.stripe.com; style-src 'self'; connect-src 'self' https://api.stripe.com https://checkout.stripe.com; frame-src https://js.stripe.com https://*.js.stripe.com https://hooks.stripe.com https://checkout.stripe.com; img-src 'self' https://*.stripe.com; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/recipient", include_in_schema=False)
def recipient():
    return FileResponse(
        STATIC_DIRECTORY / "recipient.html",
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/")
def root():
    return {
        "message": f"Welcome to {settings.APP_NAME} API",
        "environment": settings.APP_ENV,
        "status": "running",
    }


@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "app": settings.APP_NAME,
        "environment": settings.APP_ENV,
    }


@app.get("/health/ready")
def readiness_probe(db=Depends(get_db)):
    ready = database_ready(db)
    return JSONResponse(
        {"status": "ready" if ready else "unavailable"},
        status_code=200 if ready else 503,
        headers={"Cache-Control": "no-store"},
    )
