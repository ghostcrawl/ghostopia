"""ghostopia-server — FastAPI + websockets host.

The single server-side home of the GhostCrawl Python SDK + every credential. It
exposes the authenticated WS gateway (``/ws``) the thin TS renderer talks to, the SSRF
gate for user-submitted mission targets, and the strict inbound-message boundary. Import
``create_app`` to build the app; ``auth`` / ``ssrf`` / ``schemas`` are the security seams.
"""

from __future__ import annotations

from . import auth, config, db, schemas, ssrf
from .app import create_app
from .frame_fanout import FrameFanout, SessionRegistry, select_ghost_frames
from .gc_event_source import RealTaskRuntime, create_live_app, run_real_task
from .results import ResultRecorder
from .task_routes import register_task_routes
from .ws_gateway import WsGateway, start_ws_gateway

__all__ = [
    "FrameFanout",
    "RealTaskRuntime",
    "ResultRecorder",
    "SessionRegistry",
    "WsGateway",
    "auth",
    "config",
    "create_app",
    "create_live_app",
    "db",
    "register_task_routes",
    "run_real_task",
    "schemas",
    "select_ghost_frames",
    "ssrf",
    "start_ws_gateway",
]
