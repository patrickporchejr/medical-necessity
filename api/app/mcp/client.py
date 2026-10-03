"""The one way to open an MCP client session, for the API and the evals alike.

With a URL it connects to a running server over streamable HTTP (the compose `mcp` service);
without one it starts the server in process over the local Synthea bundles.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

import anyio
import httpx
from mcp import ClientSession
from mcp.shared.exceptions import McpError
from mcp.shared.memory import create_connected_server_and_client_session

from app.config import settings

log = logging.getLogger(__name__)


@asynccontextmanager
async def connect(mcp_url: str | None):
    if not mcp_url:
        from app.mcp.server import build_server

        server = build_server(settings.fhir_dir, settings.criteria_dir)
        async with create_connected_server_and_client_session(server._mcp_server) as session:
            yield session
    else:
        from mcp.client.streamable_http import streamablehttp_client

        async with streamablehttp_client(mcp_url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


class McpConnection:
    """A long-lived tool session for the API that survives the server going away (the mcp
    container restarting, or not accepting connections yet when the API starts).

    One supervisor task owns the session: the MCP client's task groups must be entered and exited
    in the same task, so a request that finds the session broken only signals it, and the
    supervisor reconnects. A call that failed on a broken session is retried once on the new one;
    every tool is read-only, so a retry is safe.
    """

    RECONNECT_SECONDS = 1.0
    READY_TIMEOUT_SECONDS = 30.0

    def __init__(self, open_session):
        self._open = open_session  # () -> async context manager yielding a ClientSession
        self._session = None
        self._ready = asyncio.Event()
        self._broken = asyncio.Event()

    async def run(self) -> None:
        """The supervisor. Runs until cancelled."""
        while True:
            try:
                async with self._open() as session:
                    self._session = session
                    self._broken.clear()
                    self._ready.set()
                    await self._broken.wait()
            except asyncio.CancelledError:
                raise
            except Exception as err:  # unreachable server, dropped session: try again shortly
                log.warning("MCP connection lost (%s); reconnecting", type(err).__name__)
            self._ready.clear()
            self._session = None
            await asyncio.sleep(self.RECONNECT_SECONDS)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        for attempt in (1, 2):
            await asyncio.wait_for(self._ready.wait(), self.READY_TIMEOUT_SECONDS)
            session = self._session
            try:
                return await session.call_tool(name, arguments)
            except (McpError, httpx.TransportError, anyio.ClosedResourceError, anyio.BrokenResourceError):
                if attempt == 2:
                    raise
                if self._session is session:  # first to notice: hand it to the supervisor
                    self._ready.clear()
                    self._broken.set()
