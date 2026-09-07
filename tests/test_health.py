from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from annotation_app.main import CLIENT_SESSION_COOKIE, create_app
from annotation_app.watcher import DirectorySyncService


class HealthApiTests(unittest.TestCase):
    def test_health_uses_existing_session_without_creating_probe_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            initial_dir = root / "initial"
            selected_dir = root / "0904"
            initial_dir.mkdir()
            selected_dir.mkdir()
            app = create_app(initial_dir, allowed_data_roots=(root,))

            with TestClient(app) as client:
                anonymous_health = client.get("/api/v1/health")
                self.assertNotIn("set-cookie", anonymous_health.headers)
                self.assertEqual(len(app.state.client_sessions), 0)

                config = client.get("/api/v1/config")
                self.assertIsNotNone(
                    config.cookies.get(CLIENT_SESSION_COOKIE)
                )
                selected = client.post(
                    "/api/v1/data-directory/select",
                    headers={"X-Requested-With": "annotation-ui"},
                    json={
                        "path": str(selected_dir),
                        "directory_generation": 0,
                    },
                )
                self.assertEqual(selected.status_code, 200, selected.text)

                session_health = client.get("/api/v1/health")
                self.assertEqual(
                    session_health.json()["data_dir"],
                    selected_dir.as_posix(),
                )
                self.assertEqual(
                    session_health.json()["directory_generation"],
                    1,
                )

                client.cookies.clear()
                probe_health = client.get("/api/v1/health")
                self.assertNotIn("set-cookie", probe_health.headers)
                self.assertEqual(probe_health.json()["data_dir"], initial_dir.as_posix())
                self.assertEqual(probe_health.json()["directory_generation"], 0)
                self.assertEqual(len(app.state.client_sessions), 1)

    def test_health_is_available_while_initial_scan_is_running(self) -> None:
        scan_started = threading.Event()
        release_scan = threading.Event()
        response_ready = threading.Event()
        result: dict[str, object] = {}
        errors: list[BaseException] = []

        async def blocked_prime(_service: DirectorySyncService) -> None:
            scan_started.set()
            await asyncio.to_thread(release_scan.wait)

        def request_health(data_dir: Path) -> None:
            try:
                with TestClient(
                    create_app(data_dir, wait_for_initial_scan=False)
                ) as client:
                    response = client.get("/api/v1/health")
                    result["status_code"] = response.status_code
                    result["payload"] = response.json()
                    response_ready.set()
                    release_scan.wait(timeout=3)
            except BaseException as exc:
                errors.append(exc)
                response_ready.set()

        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary).resolve()
            with patch.object(DirectorySyncService, "_prime_directory", blocked_prime):
                thread = threading.Thread(
                    target=request_health,
                    args=(data_dir,),
                    daemon=True,
                )
                thread.start()
                try:
                    self.assertTrue(scan_started.wait(timeout=2))
                    self.assertTrue(
                        response_ready.wait(timeout=2),
                        "健康接口被初始图片扫描阻塞。",
                    )
                finally:
                    release_scan.set()
                    thread.join(timeout=5)

        self.assertFalse(thread.is_alive())
        if errors:
            raise errors[0]
        self.assertEqual(result["status_code"], 200)
        self.assertEqual(result["payload"]["status"], "ok")
        self.assertFalse(result["payload"]["initial_scan_complete"])

    def test_degraded_data_directory_returns_service_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary).resolve()
            with TestClient(create_app(data_dir)) as client:
                original_access = os.access

                def deny_write(path: object, mode: int) -> bool:
                    if Path(path) == data_dir and mode == os.W_OK:
                        return False
                    return original_access(path, mode)

                with patch("annotation_app.main.os.access", side_effect=deny_write):
                    response = client.get("/api/v1/health")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "degraded")
        self.assertFalse(response.json()["data_dir_writable"])


if __name__ == "__main__":
    unittest.main()
