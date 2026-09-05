from __future__ import annotations

import asyncio
import os
import re
import stat
import sys
import tempfile
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from fastapi.testclient import TestClient
from PIL import Image

from annotation_app.config import MISSING_REVISION
from annotation_app.main import create_app
from annotation_app.models import AnnotationValues
from annotation_app.repository import AnnotationRepository
from annotation_app.watcher import DirectorySyncService


def sample_annotations(food_name: str = "包子") -> dict[str, object]:
    return {
        "food_name": food_name,
        "food_count": 2,
        "quality": 123.5,
        "device_model": "C9277A",
        "container_type": ["陶瓷容器"],
        "accessory_type": ["烤盘"],
        "rack_level": ["2"],
        "food_size": 8,
    }


class LinuxStatisticsTests(unittest.TestCase):
    def test_statistics_and_frontend_realtime_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary).resolve()
            for name in ("a.jpg", "b.jpg", "c.jpg"):
                Image.new("RGB", (8, 8), "red").save(data_dir / name)
            (data_dir / "b.json").write_text("{invalid", encoding="utf-8")

            with TestClient(
                create_app(data_dir, allowed_data_roots=(data_dir,))
            ) as client:
                saved = client.put(
                    "/api/v1/annotation",
                    params={"image_id": "a.jpg", "directory_generation": 0},
                    json={
                        "revision": MISSING_REVISION,
                        "annotations": sample_annotations(),
                    },
                )
                self.assertEqual(saved.status_code, 200, saved.text)

                response = client.get(
                    "/api/v1/statistics",
                    params={"directory_generation": 0},
                )
                self.assertEqual(response.status_code, 200, response.text)
                payload = response.json()
                self.assertEqual(
                    payload["summary"],
                    {
                        "total": 3,
                        "labeled": 2,
                        "valid": 1,
                        "unlabeled": 1,
                        "invalid": 1,
                    },
                )
                self.assertEqual(payload["directory_generation"], 0)
                self.assertEqual(len(payload["fields"]), 8)
                food_name = next(
                    field
                    for field in payload["fields"]
                    if field["name"] == "food_name"
                )
                self.assertEqual(
                    food_name["values"],
                    [
                        {
                            "value": "包子",
                            "label": "包子",
                            "count": 1,
                            "percentage": 100.0,
                            "is_missing": False,
                        }
                    ],
                )

                stale = client.get(
                    "/api/v1/statistics",
                    params={"directory_generation": 1},
                )
                self.assertEqual(stale.status_code, 409, stale.text)
                self.assertEqual(
                    stale.json()["detail"]["code"],
                    "data_directory_changed",
                )

                html = client.get("/").text
                script = client.get("/static/app.js").text
                directory_css = client.get("/static/directory-browser.css")
                self.assertEqual(directory_css.status_code, 200)
                self.assertEqual(
                    directory_css.headers["cache-control"],
                    "no-store, max-age=0",
                )
                routes = {route.path for route in client.app.routes}

            self.assertIn("/api/v1/statistics", routes)
            self.assertIn("/api/v1/image-events", routes)
            self.assertIn('id="openStatisticsButton"', html)
            self.assertIn('id="statisticsDialog"', html)
            self.assertIn('id="dataDirectoryDialog"', html)
            self.assertIn("new window.EventSource", script)
            self.assertIn('withDirectoryGeneration("/statistics")', script)
            self.assertIn("function openDataDirectoryDialog", script)
            self.assertIn("function browseDataDirectories", script)

            referenced_ids = set(re.findall(r'byId\("([^"]+)"\)', script))
            html_ids = set(re.findall(r'\bid="([^"]+)"', html))
            self.assertEqual(referenced_ids - html_ids, set())


class DirectorySyncServiceTests(unittest.TestCase):
    def test_switch_invalidates_old_subscriber_and_new_image_is_announced(self) -> None:
        async def scenario(old_dir: Path, new_dir: Path) -> None:
            service = DirectorySyncService(
                debounce_seconds=0.02,
                stability_interval=0.05,
                stable_samples=2,
                observer_reconcile_interval=1.0,
                fallback_poll_interval=0.25,
            )
            await service.start(old_dir, 4)
            old_token, old_queue = service.subscribe(4)
            try:
                await service.switch_directory(new_dir, 5)
                invalidation = await asyncio.wait_for(old_queue.get(), timeout=2)
                self.assertEqual(invalidation.kind, "directory-changed")
                self.assertEqual(invalidation.directory_generation, 4)
                self.assertEqual(
                    invalidation.details["current_directory_generation"],
                    5,
                )

                new_token, new_queue = service.subscribe(5)
                try:
                    image_path = new_dir / "新增 图片.jpg"
                    Image.new("RGB", (8, 8), "red").save(image_path)
                    deadline = asyncio.get_running_loop().time() + 6
                    announced = None
                    while announced is None:
                        remaining = deadline - asyncio.get_running_loop().time()
                        if remaining <= 0:
                            self.fail("实时目录监听未发布新增图片事件。")
                        change = await asyncio.wait_for(
                            new_queue.get(), timeout=remaining
                        )
                        if change.kind == "created":
                            announced = change
                    self.assertEqual(announced.image_id, image_path.name)
                    self.assertEqual(announced.directory_generation, 5)
                finally:
                    service.unsubscribe(new_token)
            finally:
                service.unsubscribe(old_token)
                await service.stop()

        with (
            tempfile.TemporaryDirectory() as old_temporary,
            tempfile.TemporaryDirectory() as new_temporary,
        ):
            asyncio.run(
                scenario(
                    Path(old_temporary).resolve(),
                    Path(new_temporary).resolve(),
                )
            )


@unittest.skipUnless(os.name == "posix", "文件权限语义仅在 POSIX 上验证")
class LinuxSidecarPermissionTests(unittest.TestCase):
    def test_new_file_honours_umask_and_replace_preserves_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary).resolve()
            image = data_dir / "permission.jpg"
            Image.new("RGB", (8, 8), "red").save(image)
            repository = AnnotationRepository.open_existing(data_dir)
            values = AnnotationValues.model_validate(sample_annotations())

            original_umask = os.umask(0o002)
            try:
                _, first_revision = repository.save_annotation(
                    image.name,
                    values,
                    MISSING_REVISION,
                )
            finally:
                os.umask(original_umask)

            sidecar = image.with_suffix(".json")
            self.assertEqual(stat.S_IMODE(sidecar.stat().st_mode), 0o664)

            sidecar.chmod(0o640)
            repository.save_annotation(image.name, values, first_revision)
            self.assertEqual(stat.S_IMODE(sidecar.stat().st_mode), 0o640)


if __name__ == "__main__":
    unittest.main()
