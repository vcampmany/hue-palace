"""Read-only web status page: GET / , /api/status (JSON), /api/events (Server-Sent Events)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from importlib.resources import files
from typing import TYPE_CHECKING

from aiohttp import web

if TYPE_CHECKING:
    from .config import WebConfig
    from .state import AppState

log = logging.getLogger(__name__)

STATE_KEY = web.AppKey("state", object)
HEARTBEAT_SECONDS = 15


async def index(_request: web.Request) -> web.Response:
    html = files("hue_palace").joinpath("static/index.html").read_text(encoding="utf-8")
    return web.Response(text=html, content_type="text/html", headers={"Cache-Control": "no-cache"})


async def status(request: web.Request) -> web.Response:
    state: AppState = request.app[STATE_KEY]
    return web.json_response(state.snapshot())


async def events(request: web.Request) -> web.StreamResponse:
    state: AppState = request.app[STATE_KEY]
    resp = web.StreamResponse(
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
    )
    await resp.prepare(request)
    queue = state.subscribe()

    async def send(kind: str, payload: object) -> None:
        await resp.write(f"event: {kind}\ndata: {json.dumps(payload)}\n\n".encode())

    try:
        await send("status", state.snapshot())
        while True:
            try:
                kind, payload = await asyncio.wait_for(queue.get(), HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                await resp.write(b": ping\n\n")
                continue
            await send(kind, payload)
    except (ConnectionResetError, ConnectionError):
        pass  # browser went away
    finally:
        state.unsubscribe(queue)
    return resp


def make_app(state: AppState) -> web.Application:
    app = web.Application()
    app[STATE_KEY] = state
    app.router.add_get("/", index)
    app.router.add_get("/api/status", status)
    app.router.add_get("/api/events", events)
    return app


async def serve(state: AppState, config: WebConfig) -> None:
    """Run the web server until cancelled."""
    runner = web.AppRunner(make_app(state), handle_signals=False, shutdown_timeout=1)
    await runner.setup()
    site = web.TCPSite(runner, config.host, config.port)
    await site.start()
    log.info("status page on http://%s:%s/", config.host, config.port)
    try:
        await asyncio.Event().wait()
    finally:
        with contextlib.suppress(Exception):
            await runner.cleanup()
