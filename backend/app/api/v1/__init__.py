from app.api.v1.auth import router as auth_router
from app.api.v1.institutions import router as institutions_router

__all__ = ["auth_router", "institutions_router"]
