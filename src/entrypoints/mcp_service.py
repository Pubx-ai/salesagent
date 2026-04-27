"""
MCP service entrypoint — serves /mcp* and /a2a* traffic only.

Used as ECS entry_point for salesagent-mcp.
Local dev: docker-compose uses src/app.py which serves everything.
"""

import json
import logging
import os
import re

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.routing import BaseRoute, Route

from src.core.main import mcp

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ---------------------------------------------------------------------------
# MCP sub-application
# ---------------------------------------------------------------------------

mcp_app = mcp.http_app(path="/")

app = FastAPI(
    title="AdCP Sales Agent — MCP",
    description="MCP and A2A endpoints only.",
    lifespan=mcp_app.lifespan,
)

app.mount("/mcp", mcp_app)


@app.api_route("/mcp", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
async def mcp_trailing_slash_redirect(request: Request):
    url = request.url.replace(path="/mcp/")
    return RedirectResponse(url=str(url), status_code=307)


# ---------------------------------------------------------------------------
# AdCP exception handler
# ---------------------------------------------------------------------------

from src.core.exceptions import AdCPError  # noqa: E402


@app.exception_handler(AdCPError)
async def adcp_error_handler(request: Request, exc: AdCPError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.to_dict())


# ---------------------------------------------------------------------------
# A2A routes
# ---------------------------------------------------------------------------

from a2a.server.apps.jsonrpc.starlette_app import A2AStarletteApplication  # noqa: E402

from src.a2a_server.adcp_a2a_server import AdCPRequestHandler, create_agent_card  # noqa: E402
from src.a2a_server.context_builder import AdCPCallContextBuilder  # noqa: E402
from src.core.domain_config import get_a2a_server_url, get_sales_agent_domain  # noqa: E402
from src.core.http_utils import get_header_case_insensitive as _get_header_case_insensitive  # noqa: E402

_agent_card = create_agent_card()
_request_handler = AdCPRequestHandler()

a2a_app = A2AStarletteApplication(
    agent_card=_agent_card,
    http_handler=_request_handler,
    context_builder=AdCPCallContextBuilder(),
)

a2a_app.add_routes_to_app(
    app,
    agent_card_url="/.well-known/agent-card.json",
    rpc_url="/a2a",
    extended_agent_card_url="/agent.json",
)


@app.api_route("/a2a/", methods=["GET", "POST", "OPTIONS"])
async def a2a_trailing_slash_redirect(request: Request):
    url = request.url.replace(path="/a2a")
    return RedirectResponse(url=str(url), status_code=307)


# ---------------------------------------------------------------------------
# Dynamic agent card — tenant-specific URL from request headers
# ---------------------------------------------------------------------------

_VALID_HOSTNAME_RE = re.compile(
    r"^[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?)*(\:\d{1,5})?$"
)


def _is_valid_hostname(value: str) -> bool:
    return bool(value) and len(value) <= 253 and _VALID_HOSTNAME_RE.match(value) is not None


def _create_dynamic_agent_card(request: Request):
    def get_protocol(hostname: str) -> str:
        return "http" if hostname.startswith("localhost") or hostname.startswith("127.0.0.1") else "https"

    apx_incoming_host = _get_header_case_insensitive(request.headers, "Apx-Incoming-Host")
    if apx_incoming_host and not _is_valid_hostname(apx_incoming_host):
        apx_incoming_host = None
    if apx_incoming_host:
        protocol = get_protocol(apx_incoming_host)
        server_url = f"{protocol}://{apx_incoming_host}/a2a"
    else:
        host = _get_header_case_insensitive(request.headers, "Host") or ""
        if host and not _is_valid_hostname(host):
            host = ""
        sales_domain = get_sales_agent_domain()
        if host and host != sales_domain:
            protocol = get_protocol(host)
            server_url = f"{protocol}://{host}/a2a"
        else:
            server_url = get_a2a_server_url() or "http://localhost:8080/a2a"

    dynamic_card = _agent_card.model_copy()
    dynamic_card.url = server_url
    return dynamic_card


_AGENT_CARD_PATHS = {"/.well-known/agent-card.json", "/.well-known/agent.json", "/agent.json"}


def _replace_routes() -> None:
    async def dynamic_agent_card(request: Request):
        card = _create_dynamic_agent_card(request)
        return JSONResponse(card.model_dump(mode="json"))

    new_routes: list[BaseRoute] = []
    for route in app.routes:
        path = getattr(route, "path", None)
        if path in _AGENT_CARD_PATHS:
            new_routes.append(Route(path, dynamic_agent_card, methods=["GET", "OPTIONS"]))
        else:
            new_routes.append(route)
    app.router.routes = new_routes


_replace_routes()


# ---------------------------------------------------------------------------
# A2A messageId compatibility middleware
# ---------------------------------------------------------------------------


@app.middleware("http")
async def a2a_messageid_compatibility_middleware(request: Request, call_next):
    if request.url.path == "/a2a" and request.method == "POST":
        body = await request.body()
        try:
            data = json.loads(body)
            if isinstance(data, dict) and "params" in data:
                params = data.get("params", {})
                if "message" in params and isinstance(params["message"], dict):
                    message = params["message"]
                    if "messageId" in message and isinstance(message["messageId"], (int, float)):
                        message["messageId"] = str(message["messageId"])
                        body = json.dumps(data).encode()
            if "id" in data and isinstance(data["id"], (int, float)):
                data["id"] = str(data["id"])
                body = json.dumps(data).encode()
        except (json.JSONDecodeError, KeyError, TypeError):
            pass

        from starlette.requests import Request as StarletteRequest

        async def _receive():
            return {"type": "http.request", "body": body}

        request = StarletteRequest(request.scope, receive=_receive)

    return await call_next(request)


# ---------------------------------------------------------------------------
# Health routes
# ---------------------------------------------------------------------------

from src.routes.health import debug_router as health_debug_router  # noqa: E402
from src.routes.health import router as health_router  # noqa: E402

app.include_router(health_router)
app.include_router(health_debug_router)

# ---------------------------------------------------------------------------
# Middleware (outermost last)
# ---------------------------------------------------------------------------

from src.core.auth_middleware import UnifiedAuthMiddleware  # noqa: E402

app.add_middleware(UnifiedAuthMiddleware)

_cors_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:8000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _cors_origins],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
