"""FastAPI application factory for OpsPilot 2.0 Web Command Center."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from opspilot.config import Settings
from opspilot.web.api import router as api_router
from opspilot.web.auth import SESSION_COOKIE_NAME, get_client_ip, get_current_session
from opspilot.web.security import create_session_manager, login_rate_limiter, verify_password

STATIC_DIR = Path(__file__).parent / "static"


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=200)


def create_web_app(settings: Settings | None = None, channel: Any = None) -> FastAPI:
    """Create and configure the FastAPI Web Command Center application."""
    if settings is None:
        from opspilot.config import load_settings

        settings = load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from datetime import datetime, timedelta

        from opspilot.db.endpoints import seed_endpoints_from_yaml
        from opspilot.db.engine import init_db
        from opspilot.db.renewals import seed_renewals_from_yaml

        await init_db()
        if settings and settings.monitoring:
            yaml_renewals = [r.model_dump() for r in getattr(settings.monitoring, "initial_renewals", [])]
            yaml_endpoints = [e.model_dump() for e in getattr(settings.monitoring, "http_endpoints", [])]

            if not yaml_endpoints:
                yaml_endpoints = [
                    {
                        "name": "IAM Auth Gateway",
                        "url": "https://auth.iitdeveloper.com/health",
                        "expected_status": 200,
                        "timeout_seconds": 5,
                    },
                    {
                        "name": "Sendrin Notification API",
                        "url": "https://api.iitdeveloper.com/health",
                        "expected_status": 200,
                        "timeout_seconds": 5,
                    },
                    {
                        "name": "Growixa Web Platform",
                        "url": "https://growixa.com/",
                        "expected_status": 200,
                        "timeout_seconds": 5,
                    },
                ]
            if not yaml_renewals:
                today = datetime.now()
                yaml_renewals = [
                    {
                        "name": "OVH Production Cloud VPS",
                        "category": "vps",
                        "amount": 42.0,
                        "currency": "USD",
                        "due_date": (today + timedelta(days=18)).strftime("%Y-%m-%d"),
                        "recurrence": "monthly",
                        "notes": "Primary host node",
                    },
                    {
                        "name": "Wildcard *.iitdeveloper.com SSL",
                        "category": "ssl",
                        "amount": 0.0,
                        "currency": "USD",
                        "due_date": (today + timedelta(days=64)).strftime("%Y-%m-%d"),
                        "recurrence": "quarterly",
                        "notes": "Let's Encrypt automated TLS",
                    },
                    {
                        "name": "Sendrin Domain Portfolio",
                        "category": "domain",
                        "amount": 14.99,
                        "currency": "USD",
                        "due_date": (today + timedelta(days=142)).strftime("%Y-%m-%d"),
                        "recurrence": "yearly",
                        "notes": "Namecheap DNS",
                    },
                ]
            await seed_endpoints_from_yaml(yaml_endpoints)
            await seed_renewals_from_yaml(yaml_renewals)

            # Seed default tracked domains
            from opspilot.db.domains import seed_domains

            await seed_domains(
                [
                    "auth.iitdeveloper.com",
                    "sendrin.com",
                    "growixa.com",
                    "iitdeveloper.com",
                ]
            )
        yield

    app = FastAPI(
        title="OpsPilot 2.0 Command Center",
        description="Autonomous Infrastructure & Billing Command Center",
        version="0.4.0",
        docs_url=None,  # Disabled for security by default
        redoc_url=None,
        lifespan=lifespan,
    )

    # Attach state
    app.state.settings = settings
    app.state.channel = channel
    app.state.admin_password = settings.admin_password
    app.state.session_manager = create_session_manager(settings.admin_password)

    # Security headers middleware
    @app.middleware("http")
    async def add_security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data:; "
            "connect-src 'self';"
        )
        return response

    # ── Auth Endpoints ────────────────────────────────────────────────────────

    @app.post("/api/auth/login", tags=["Auth"])
    async def login(req: LoginRequest, request: Request, response: Response) -> dict:
        client_ip = get_client_ip(request)

        # Rate limiting check
        is_locked, remaining_sec = login_rate_limiter.is_locked(client_ip)
        if is_locked:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Too many failed login attempts. Account temporarily locked. Try again in {remaining_sec}s.",
                headers={"Retry-After": str(remaining_sec)},
            )

        # Constant-time password verification
        if not verify_password(req.password, app.state.admin_password):
            login_rate_limiter.record_failure(client_ip)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid administrator password.",
            )

        # Clear failures and establish session
        login_rate_limiter.reset(client_ip)
        session_token, csrf_token = app.state.session_manager.create_session(user="admin")

        # Set secure HTTP-only cookie
        # In development/localhost over HTTP, secure=False allows local testing if not behind TLS;
        # when behind Caddy proxy or production, secure=True is enforced.
        is_https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=session_token,
            max_age=28800,  # 8 hours
            httponly=True,
            secure=is_https,
            samesite="strict" if is_https else "lax",
            path="/",
        )
        return {
            "success": True,
            "message": "Authenticated successfully.",
            "csrf_token": csrf_token,
            "server_name": settings.server_name,
        }

    @app.post("/api/auth/logout", tags=["Auth"])
    async def logout(response: Response) -> dict:
        response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
        return {"success": True, "message": "Logged out."}

    @app.get("/api/auth/me", tags=["Auth"])
    async def get_me(session: Annotated[dict, Depends(get_current_session)]) -> dict:
        return {
            "authenticated": True,
            "user": session.get("user", "admin"),
            "csrf_token": session.get("csrf", ""),
            "server_name": settings.server_name,
        }

    # Mount API routers
    app.include_router(api_router)

    # Static assets and SPA fallback
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        index_file = STATIC_DIR / "index.html"
        if not index_file.exists():
            return FileResponse(Path(__file__).parent / "static" / "index.html")
        return FileResponse(index_file)

    return app
