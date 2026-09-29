from __future__ import annotations

from io import BytesIO
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from annotation_app.main import ImageSnapshotResponse, create_app
from annotation_app.repository import AnnotationRepository
from annotation_app.watcher import DirectorySyncService
import annotation_app.main as main


def jpeg_bytes(color: str = "red") -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (64, 64), color).save(buffer, format="JPEG")
    return buffer.getvalue()


class ImageReadinessApiTests(unittest.TestCase):
    def test_directory_switch_lists_nested_paths_without_opening_images(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            initial = root / "initial"
            selected = root / "selected"
            nested = selected / "one" / "two"
            initial.mkdir()
            nested.mkdir(parents=True)
            full = jpeg_bytes()
            (initial / "old.jpg").write_bytes(full)
            (selected / "first.jpg").write_bytes(full)
            (nested / "clicked.jpg").write_bytes(full)
            (nested / "broken.jpg").write_bytes(full[:-20])
            image_opens = []
            original_open = Path.open
            original_image_open = Image.open

            def metadata_only_open(path, *args, **kwargs):
                if path.suffix.lower() == ".jpg":
                    image_opens.append(path)
                    raise AssertionError("目录扫描不应打开图片内容")
                return original_open(path, *args, **kwargs)

            with (
                patch.object(Path, "open", metadata_only_open),
                patch("PIL.Image.open", side_effect=AssertionError("目录扫描不应解码图片")) as decoder,
                TestClient(create_app(initial, allowed_data_roots=(root,))) as client,
            ):
                initial_list = client.get("/api/v1/images", params={"directory_generation": 0})
                self.assertEqual(initial_list.status_code, 200, initial_list.text)
                self.assertEqual(initial_list.json()["summary"]["total"], 1)
                # 切换只建立一次路径索引，不预先生成完整标注列表。
                with patch.object(AnnotationRepository, "list_images", side_effect=AssertionError("重复读取列表")):
                    switched = client.post(
                        "/api/v1/data-directory/select",
                        headers={"X-Requested-With": "annotation-ui"},
                        json={"path": str(selected), "directory_generation": 0},
                    )
                self.assertEqual(switched.status_code, 200, switched.text)
                params = {"directory_generation": 1}
                listed = client.get("/api/v1/images", params=params).json()
                self.assertEqual(
                    {item["image_id"] for item in listed["items"]},
                    {"first.jpg", "one/two/clicked.jpg", "one/two/broken.jpg"},
                )
                self.assertEqual(client.get("/api/v1/statistics", params=params).json()["summary"]["total"], 3)
                self.assertEqual(image_opens, [])
                decoder.assert_not_called()

                # 恢复图片 IO 后，只有被点击的图片触发完整性校验。
                with (
                    patch.object(Path, "open", original_open),
                    patch("PIL.Image.open", original_image_open),
                    patch.object(DirectorySyncService, "_contents_complete", wraps=DirectorySyncService._contents_complete) as validate,
                ):
                    preview = client.get(
                        "/api/v1/image",
                        params={**params, "image_id": "one/two/clicked.jpg"},
                    )
                self.assertEqual(preview.status_code, 200, preview.text)
                self.assertEqual(preview.content, full)
                self.assertEqual([call.args[0].image_id for call in validate.call_args_list], ["one/two/clicked.jpg"])

    def test_preview_uses_complete_snapshot_if_original_is_replaced_before_send(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            photo = root / "replace.jpg"
            full = jpeg_bytes()
            photo.write_bytes(full)
            responses = []
            send = ImageSnapshotResponse.__call__

            async def overwrite_before_send(response, scope, receive, sender):
                responses.append(response)
                photo.write_bytes(jpeg_bytes("blue")[:-20])
                await send(response, scope, receive, sender)

            with TestClient(create_app(root)) as client:
                with patch.object(ImageSnapshotResponse, "__call__", overwrite_before_send):
                    response = client.get("/api/v1/image", params={"image_id": photo.name, "directory_generation": 0})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, full)
            self.assertTrue(responses[0].snapshot.closed)

    def test_overwrite_during_preview_copy_returns_retry_and_closes_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            photo = root / "replace.jpg"
            photo.write_bytes(jpeg_bytes())
            copied = []
            original_copy = main.shutil.copyfileobj

            def overwrite_after_copy(source, destination, **kwargs):
                copied.append(destination)
                original_copy(source, destination, **kwargs)
                photo.write_bytes(jpeg_bytes("blue")[:-20])

            with TestClient(create_app(root)) as client:
                with patch.object(main.shutil, "copyfileobj", overwrite_after_copy):
                    response = client.get("/api/v1/image", params={"image_id": photo.name, "directory_generation": 0})
            self.assertEqual(response.status_code, 425, response.text)
            self.assertTrue(copied[0].closed)

    def test_partial_image_is_listed_but_preview_waits_until_complete(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            full = jpeg_bytes()
            photo = root / "新照片.jpg"
            photo.write_bytes(full[:-20])
            with TestClient(create_app(root)) as client:
                params = {"directory_generation": 0}
                self.assertEqual(
                    [item["name"] for item in client.get("/api/v1/images", params=params).json()["items"]],
                    [photo.name],
                )
                self.assertEqual(client.get("/api/v1/statistics", params=params).json()["summary"]["total"], 1)
                preview_params = {**params, "image_id": photo.name}
                preview = client.get("/api/v1/image", params=preview_params)
                self.assertEqual(preview.status_code, 425, preview.text)

                started = time.monotonic()
                photo.write_bytes(full)
                payload = None
                while time.monotonic() - started < 3:
                    payload = client.get("/api/v1/images", params=params).json()
                    if payload["items"]:
                        break
                    time.sleep(0.03)
                self.assertEqual([item["name"] for item in payload["items"]], [photo.name])
                self.assertEqual(payload["summary"]["total"], 1)
                self.assertEqual(client.get("/api/v1/image", params=preview_params).content, full)

                # The old ready ID must not allow serving a newly truncated overwrite.
                photo.write_bytes(jpeg_bytes("blue")[:-20])
                preview = client.get("/api/v1/image", params=preview_params)
                self.assertEqual(preview.status_code, 425, preview.text)


if __name__ == "__main__":
    unittest.main()
