from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1.academic_records import router as academic_records_router
from app.api.v1.auth import router as auth_router
from app.api.v1.catalog import router as catalog_router
from app.api.v1.institutions import router as institutions_router
from app.api.v1.staff import router as staff_router
from app.core.config import settings

app = FastAPI(
    title=f"{settings.APP_NAME} API",
    description="Academic transcript request and verification platform for Kenya.",
    version="0.1.0",
)

app.include_router(auth_router, prefix="/api/v1")
app.include_router(institutions_router, prefix="/api/v1")
app.include_router(staff_router, prefix="/api/v1")
app.include_router(catalog_router, prefix="/api/v1")
app.include_router(academic_records_router, prefix="/api/v1")

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
        ("/api/v1/me/", "/api/v1/staff/", "/api/v1/auth/", "/api/v1/admin/")
    ):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/workspace", include_in_schema=False)
def workspace():
    return FileResponse(
        STATIC_DIRECTORY / "workspace.html",
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
