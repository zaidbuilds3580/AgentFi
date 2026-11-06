"""
Health check endpoints for server liveness and Supabase database connectivity.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.api.deps import get_db
from backend.core.config import settings
from backend.core.logging import logger

router = APIRouter(tags=["Health"])


@router.get("/health", summary="Application Health Check")
def check_health() -> dict:
    """
    Returns application status and current environment.
    """
    return {
        "status": "ok",
        "environment": settings.ENVIRONMENT,
    }


@router.get("/health/db", summary="Database Connectivity Check")
def check_database_health(db: Session = Depends(get_db)) -> dict:
    """
    Executes a lightweight ping (SELECT 1) against the Supabase PostgreSQL database.
    Catches connection failures and returns a clean 503 without exposing secrets.
    """
    try:
        result = db.execute(text("SELECT 1")).scalar()
        if result == 1:
            return {"database": "connected"}
        raise RuntimeError("Unexpected query result from database")
    except Exception as e:
        logger.error(f"Database health check failed: {type(e).__name__}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "database": "disconnected",
                "message": "Database connection failed.",
                "troubleshooting_hints": [
                    "Verify your Supabase project is not paused in the Supabase Dashboard.",
                    "Ensure you are using the Session Pooler URL (host ending in pooler.supabase.com, port 5432).",
                    "If your password has special characters (e.g. @, #, $, %), ensure they are URL-encoded.",
                    "Check network/firewall policies for outbound TCP on port 5432."
                ],
            },
        )

