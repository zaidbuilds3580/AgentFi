"""
AgentFi FastAPI Application Entrypoint.

Provides the application factory, CORS configuration, router mounting, and lifespan management.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.router import api_router
from backend.core.config import settings
from backend.core.logging import logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifecycle management.
    Performs startup checks and logging without revealing sensitive values.
    """
    logger.info(
        f"Starting {settings.APP_NAME} | Environment: {settings.ENVIRONMENT} | "
        f"Debug: {settings.DEBUG} | API Prefix: {settings.API_PREFIX}"
    )
    yield
    logger.info(f"Shutting down {settings.APP_NAME}...")


def create_app() -> FastAPI:
    """
    FastAPI application factory.
    """
    app = FastAPI(
        title=settings.APP_NAME,
        description="Multi-agent finance operations assistant for SMEs - Backend Service",
        version="0.1.0",
        openapi_url=f"{settings.API_PREFIX}/openapi.json",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # Configure Cross-Origin Resource Sharing (CORS) for Next.js frontend
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register API routers under /api
    app.include_router(api_router, prefix=settings.API_PREFIX)

    @app.get("/", summary="Root index")
    def root():
        return {
            "app": settings.APP_NAME,
            "status": "running",
            "environment": settings.ENVIRONMENT,
            "docs": "/docs",
        }

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)

