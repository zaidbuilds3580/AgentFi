"""
AgentFi FastAPI Application Entrypoint.

Provides the application factory, CORS configuration, router mounting, and lifespan management.
"""

from contextlib import asynccontextmanager
import uuid
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from backend.api.router import api_router
from backend.core.config import settings
from backend.core.logging import logger
from backend.core.version import CONTRACT_VERSION


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifecycle management.
    Performs startup checks and logging without revealing sensitive values.
    """
    logger.info(
        f"Starting {settings.APP_NAME} | Contract: {CONTRACT_VERSION} | Environment: {settings.ENVIRONMENT} | "
        f"Debug: {settings.DEBUG} | API Prefix: {settings.API_PREFIX}"
    )
    yield
    logger.info(f"Shutting down {settings.APP_NAME}...")


def create_app() -> FastAPI:
    """
    FastAPI application factory with contract versioning, security headers,
    request-id correlation, and canonical error envelope handling.
    """
    app = FastAPI(
        title=settings.APP_NAME,
        description="Multi-agent finance operations assistant for SMEs - Backend Service",
        version=CONTRACT_VERSION,
        openapi_url=f"{settings.API_PREFIX}/openapi.json",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # 1. Request context (X-Request-ID) & Security headers middleware
    @app.middleware("http")
    async def request_context_and_security_headers_middleware(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID")
        if not request_id:
            request_id = str(uuid.uuid4())
        request.state.request_id = request_id

        response: Response = await call_next(request)

        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response

    # 2. CORS configuration for Next.js frontend
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 3. Canonical Error Envelope Handlers
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        code_map = {
            400: "BAD_REQUEST",
            401: "UNAUTHORIZED",
            403: "FORBIDDEN",
            404: "NOT_FOUND",
            409: "CONFLICT",
            413: "REQUEST_ENTITY_TOO_LARGE",
            415: "UNSUPPORTED_MEDIA_TYPE",
            422: "UNPROCESSABLE_ENTITY",
            500: "INTERNAL_SERVER_ERROR",
            503: "SERVICE_UNAVAILABLE",
        }
        err_code = code_map.get(exc.status_code, f"HTTP_{exc.status_code}")

        if isinstance(exc.detail, dict):
            msg = exc.detail.get("message", "HTTP Error")
            details = exc.detail
        else:
            msg = str(exc.detail) if exc.detail else "HTTP Error"
            details = None

        content = {
            "detail": exc.detail,
            "error": {
                "code": err_code,
                "message": msg,
                "request_id": request_id,
                "details": details,
            },
            "request_id": request_id,
        }
        headers = dict(exc.headers or {})
        headers["X-Request-ID"] = request_id
        return JSONResponse(status_code=exc.status_code, content=content, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        content = {
            "detail": exc.errors(),
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Request validation failed. Inspect 'details' for field-level errors.",
                "request_id": request_id,
                "details": exc.errors(),
            },
            "request_id": request_id,
        }
        return JSONResponse(
            status_code=422,
            content=content,
            headers={"X-Request-ID": request_id},
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        logger.error(f"[Request {request_id}] Unhandled exception: {type(exc).__name__}: {str(exc)}")
        content = {
            "detail": "Internal server error",
            "error": {
                "code": "INTERNAL_SERVER_ERROR",
                "message": "An unexpected server error occurred. Please contact support with this request ID.",
                "request_id": request_id,
                "details": None,
            },
            "request_id": request_id,
        }
        return JSONResponse(
            status_code=500,
            content=content,
            headers={"X-Request-ID": request_id},
        )

    # 4. Register API routers under /api
    app.include_router(api_router, prefix=settings.API_PREFIX)

    @app.get("/", summary="Root index")
    def root():
        return {
            "app": settings.APP_NAME,
            "status": "running",
            "contract_version": CONTRACT_VERSION,
            "environment": settings.ENVIRONMENT,
            "docs": "/docs",
        }

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)

