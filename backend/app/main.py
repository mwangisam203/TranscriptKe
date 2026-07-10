from fastapi import FastAPI

from app.api.v1.auth import router as auth_router
from app.api.v1.institutions import router as institutions_router
from app.core.config import settings


app = FastAPI(
    title=f"{settings.APP_NAME} API",
    description="Academic transcript request and verification platform for Kenya.",
    version="0.1.0",
)

app.include_router(auth_router, prefix="/api/v1")
app.include_router(institutions_router, prefix="/api/v1")


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
