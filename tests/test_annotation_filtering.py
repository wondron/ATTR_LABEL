from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from annotation_app.config import MISSING_REVISION
from annotation_app.main import create_app
from annotation_app.models import AnnotationValues
from annotation_app.repository import AnnotationRepository


class AnnotationFilteringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.repository = AnnotationRepository(self.root)

    def create_image(self, name: str) -> Path:
        path = self.root / name
        Image.new("RGB", (8, 8), "red").save(path)
        return path

    def write_annotations(self, image: Path, annotations: dict[str, object]) -> None:
        image.with_suffix(".json").write_text(
            json.dumps({"annotations": annotations}, ensure_ascii=False),
            encoding="utf-8",
        )

    def test_unlabeled_image_exposes_defaults_without_creating_sidecar(self) -> None:
        image = self.create_image("unlabeled.jpg")

        item = self.repository.list_images()[0]

        self.assertEqual(item["annotations"], AnnotationValues().model_dump(mode="json"))
        self.assertFalse(item["annotation_exists"])
        self.assertIsNone(item["annotation_valid"])
        self.assertIsNone(item["revision"])
        self.assertFalse(image.with_suffix(".json").exists())

    def test_legacy_values_are_normalized_and_missing_fields_use_defaults(self) -> None:
        image = self.create_image("legacy.jpg")
        self.write_annotations(
            image,
            {
                "food_name": "  包子  ",
                "food_count": "0",
                "quality": "123.5",
                "container_type": "锡箔纸",
                "rack_level": "底板层、2",
                "food_size": ["8"],
            },
        )

        item = self.repository.list_images()[0]

        self.assertTrue(item["annotation_valid"])
        self.assertEqual(
            item["annotations"],
            {
                "food_name": "包子",
                "food_count": 0,
                "quality": 123.5,
                "device_model": None,
                "container_type": ["铝箔纸"],
                "accessory_type": ["无"],
                "rack_level": ["0", "2"],
                "food_size": 8,
            },
        )

    def test_invalid_annotations_are_distinct_from_default_values(self) -> None:
        invalid_contents = ("{invalid", "[]", "{}", '{"annotations":{"food_count":-1}}')
        for index, content in enumerate(invalid_contents):
            image = self.create_image(f"invalid-{index}.jpg")
            image.with_suffix(".json").write_text(content, encoding="utf-8")

        items = self.repository.list_images()

        self.assertEqual(len(items), len(invalid_contents))
        for item in items:
            with self.subTest(image=item["image_id"]):
                self.assertTrue(item["annotation_exists"])
                self.assertFalse(item["annotation_valid"])
                self.assertIsNone(item["annotations"])
                self.assertTrue(item["annotation_error"])
                self.assertTrue(item["revision"])

    def test_list_reuses_document_cache_and_reflects_saved_changes(self) -> None:
        image = self.create_image("saved.jpg")
        self.write_annotations(image, {"food_name": "包子"})

        with patch.object(
            self.repository,
            "_read_uncached_document",
            wraps=self.repository._read_uncached_document,
        ) as read_document:
            first = self.repository.list_images()[0]
            cached = self.repository.list_images()[0]
            self.assertEqual(first["annotations"], cached["annotations"])
            self.assertEqual(read_document.call_count, 1)

            _, saved_revision = self.repository.save_annotation(
                image.name,
                AnnotationValues(food_name="面包", food_count=3),
                first["revision"],
            )
            updated = self.repository.list_images()[0]

        self.assertEqual(updated["annotations"]["food_name"], "面包")
        self.assertEqual(updated["annotations"]["food_count"], 3)
        self.assertEqual(updated["revision"], saved_revision)
        self.assertNotEqual(updated["revision"], first["revision"])

    def test_api_exposes_filter_values_and_refreshes_them_after_save(self) -> None:
        self.create_image("unlabeled.jpg")
        legacy = self.create_image("legacy.jpg")
        self.write_annotations(legacy, {"food_count": "2"})
        invalid = self.create_image("invalid.jpg")
        invalid.with_suffix(".json").write_text("{invalid", encoding="utf-8")

        with TestClient(create_app(self.root)) as client:
            response = client.get("/api/v1/images", params={"directory_generation": 0})
            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            items = {item["image_id"]: item for item in payload["items"]}

            self.assertEqual(
                items["unlabeled.jpg"]["annotations"],
                AnnotationValues().model_dump(mode="json"),
            )
            self.assertEqual(items["legacy.jpg"]["annotations"]["food_count"], 2)
            self.assertIsNone(items["invalid.jpg"]["annotations"])
            self.assertEqual(
                payload["summary"],
                {"total": 3, "labeled": 2, "unlabeled": 1, "invalid": 1},
            )

            saved = client.put(
                "/api/v1/annotation",
                params={"image_id": "unlabeled.jpg", "directory_generation": 0},
                json={
                    "revision": MISSING_REVISION,
                    "annotations": {"food_name": "面包", "food_count": 0},
                },
            )
            self.assertEqual(saved.status_code, 200, saved.text)
            response = client.get(
                "/api/v1/images",
                params={"directory_generation": 0, "status": "labeled", "search": "unlabeled"},
            )
            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            self.assertEqual(payload["filtered_total"], 1)
            item = payload["items"][0]
            self.assertEqual(item["annotations"], saved.json()["annotation"]["annotations"])
            self.assertEqual(item["revision"], saved.json()["revision"])
            self.assertTrue(item["annotation_valid"])


if __name__ == "__main__":
    unittest.main()
