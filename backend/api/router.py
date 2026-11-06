"""
Central API router that aggregates all domain route handlers.
"""

from fastapi import APIRouter

from backend.api import auth, health

api_router = APIRouter()

# Register health check endpoints directly under /api (e.g. /api/health, /api/health/db)
api_router.include_router(health.router)

# Register auth endpoints under /api/auth (e.g. /api/auth/register, /api/auth/login, /api/auth/me)
api_router.include_router(auth.router)

