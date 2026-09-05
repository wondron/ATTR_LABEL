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
import annotation_app.main as main


def jpeg_bytes(color: str = "red") -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (64, 64), color).save(buffer, format="JPEG")
    return buffer.getvalue()


class ImageReadinessApiTests(unittest.TestCase):
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

    def test_partial_image_is_hidden_from_list_statistics_and_preview_until_complete(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            full = jpeg_bytes()
            photo = root / "新照片.jpg"
            photo.write_bytes(full[:-20])
            with TestClient(create_app(root)) as client:
                params = {"directory_generation": 0}
                self.assertEqual(client.get("/api/v1/images", params=params).json()["items"], [])
                self.assertEqual(client.get("/api/v1/statistics", params=params).json()["summary"]["total"], 0)
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
                self.assertEqual(client.get("/api/v1/images", params=params).json()["items"], [])


if __name__ == "__main__":
    unittest.main()
