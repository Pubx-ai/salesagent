"""
Admin service entrypoint — serves /admin*, /api/v1*, /health, and landing pages.
Runs Alembic migrations on startup before accepting traffic.

Used as ECS entry_point for salesagent-admin.
Local dev: docker-compose uses src/app.py which serves everything.
"""

import asyncio
import logging
import os
import subprocess
import sys
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.routing import Route

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


# ---------------------------------------------------------------------------
# Migrations — run once before uvicorn starts.
# migrate.py handles the alembic_version table race condition with a retry
# in case two tasks start concurrently during a rolling deploy.
# ---------------------------------------------------------------------------


def run_migrations() -> None:
    logger.info("Running database migrations...")
    result = subprocess.run(
        [sys.executable, "scripts/ops/migrate.py"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        logger.error("Migration failed:\n%s", result.stderr)
        sys.exit(1)
    logger.info("Migrations complete")


# ---------------------------------------------------------------------------
# Flask admin — module-level so _install_admin_mounts can reference it
# ---------------------------------------------------------------------------

from a2wsgi import WSGIMiddleware  # noqa: E402

from src.admin.app import create_app  # noqa: E402

flask_admin_app = create_app()
admin_wsgi = WSGIMiddleware(flask_admin_app)


def _install_admin_mounts(app: FastAPI) -> None:
    from starlette.routing import Mount

    app.router.routes = [
        route
        for route in app.router.routes
        if not (isinstance(route, Mount) and isinstance(route.app, WSGIMiddleware) and route.path in {"/admin", ""})
    ]
    app.mount("/admin", admin_wsgi)  # type: ignore[arg-type]
    app.mount("/", admin_wsgi)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# App + lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def app_lifespan(app: FastAPI):
    _install_admin_mounts(app)
    logger.info("Admin service starting up")
    yield
    logger.info("Admin service shutting down")


app = FastAPI(
    title="AdCP Sales Agent — Admin",
    description="Admin UI, REST API, and landing pages.",
    lifespan=app_lifespan,
)

# ---------------------------------------------------------------------------
# AdCP exception handler
# ---------------------------------------------------------------------------

from src.core.exceptions import AdCPError  # noqa: E402


@app.exception_handler(AdCPError)
async def adcp_error_handler(request: Request, exc: AdCPError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.to_dict())


# ---------------------------------------------------------------------------
# REST API + health
# ---------------------------------------------------------------------------

from src.routes.api_v1 import router as api_v1_router  # noqa: E402
from src.routes.health import debug_router as health_debug_router  # noqa: E402
from src.routes.health import router as health_router  # noqa: E402

app.include_router(api_v1_router)
app.include_router(health_router)
app.include_router(health_debug_router)

# ---------------------------------------------------------------------------
# Landing page routes — inserted before /admin catch-all mount
# ---------------------------------------------------------------------------

from src.core.domain_routing import route_landing_page  # noqa: E402
from src.landing import generate_tenant_landing_page  # noqa: E402
from src.landing.landing_page import generate_fallback_landing_page  # noqa: E402


async def _handle_landing_page(request: Request):
    result = await asyncio.to_thread(route_landing_page, dict(request.headers))
    if result.type == "admin":
        return RedirectResponse(url="/admin/login", status_code=302)
    if result.type in ("custom_domain", "subdomain") and result.tenant:
        try:
            html_content = await asyncio.to_thread(generate_tenant_landing_page, result.tenant, result.effective_host)
            return HTMLResponse(content=html_content)
        except Exception as e:
            logger.error("Error generating landing page: %s", e, exc_info=True)
            return HTMLResponse(content=generate_fallback_landing_page("Error generating landing page"))
    if result.type == "custom_domain":
        return HTMLResponse(content=generate_fallback_landing_page(f"Domain {result.effective_host} is not configured"))
    return HTMLResponse(content=generate_fallback_landing_page("No tenant found"))


app.router.routes.insert(0, Route("/", _handle_landing_page, methods=["GET"]))
app.router.routes.insert(1, Route("/landing", _handle_landing_page, methods=["GET"]))

# ---------------------------------------------------------------------------
# Middleware (outermost last)
# ---------------------------------------------------------------------------

from src.core.auth_middleware import UnifiedAuthMiddleware  # noqa: E402
from src.routes.rest_compat_middleware import RestCompatMiddleware  # noqa: E402

app.add_middleware(UnifiedAuthMiddleware)
app.add_middleware(RestCompatMiddleware)

_cors_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:8000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _cors_origins],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Entrypoint — migrations first, then uvicorn
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    run_migrations()
    port = int(os.getenv("ADCP_SALES_PORT", "8001"))
    uvicorn.run("src.entrypoints.admin_service:app", host="0.0.0.0", port=port, log_level="info")
