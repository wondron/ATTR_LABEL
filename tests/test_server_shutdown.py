from __future__ import annotations

import asyncio
import socket
import sys
import tempfile
import unittest
from contextlib import AsyncExitStack
from pathlib import Path

import httpx
import uvicorn

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from annotation_app import create_app
from run_annotation_ui import AnnotationServer


class ServerShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_shutdown_finishes_with_idle_browser_event_streams(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            application = create_app(temporary, wait_for_initial_scan=False)
            server = AnnotationServer(
                uvicorn.Config(
                    application,
                    host="127.0.0.1",
                    port=0,
                    http="h11",
                    loop="asyncio",
                    log_level="warning",
                )
            )
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
                serving = asyncio.create_task(server.serve(sockets=[listener]))
                sessions = []
                try:
                    async with AsyncExitStack() as stack:
                        async def wait_started() -> None:
                            while not server.started:
                                if serving.done():
                                    await serving
                                    self.fail("Server stopped before listening")
                                await asyncio.sleep(0.01)

                        await asyncio.wait_for(wait_started(), timeout=5)
                        for _ in range(2):
                            client = await stack.enter_async_context(
                                httpx.AsyncClient(
                                    base_url=f"http://127.0.0.1:{port}",
                                    trust_env=False,
                                    timeout=5,
                                )
                            )
                            config = await client.get("/api/v1/config")
                            self.assertEqual(config.status_code, 200)
                            response = await stack.enter_async_context(
                                client.stream(
                                    "GET", "/api/v1/image-events",
                                    params={"directory_generation": 0},
                                )
                            )
                            self.assertEqual(response.status_code, 200)
                            lines = response.aiter_lines()
                            received = []
                            async for line in lines:
                                received.append(line)
                                if not line:
                                    break
                            self.assertIn("event: ready", received)

                        sessions = list(application.state.client_sessions.values())
                        self.assertEqual(len(sessions), 2)
                        self.assertTrue(all(session.active_streams == 1 for session in sessions))
                        server.should_exit = True
                        # Keep both browser connections open while the server stops.
                        await asyncio.wait_for(asyncio.shield(serving), timeout=3)
                finally:
                    server.should_exit = True
                    if not serving.done():
                        await asyncio.wait_for(serving, timeout=5)
                self.assertTrue(all(session.active_streams == 0 for session in sessions))
                self.assertTrue(all(not session.started for session in sessions))
                self.assertFalse(application.state.client_sessions)


if __name__ == "__main__":
    unittest.main()
