from __future__ import annotations

import sys
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from annotation_app.config import ACCESSORY_TYPES
from annotation_app.models import AnnotationValues, SaveAnnotationRequest


class AnnotationParsingTests(unittest.TestCase):
    def test_water_quality_normalizes_legacy_values_and_defaults(self) -> None:
        self.assertIsNone(AnnotationValues().water_quality)
        for raw, expected in (("", None), (None, None), (0, 0.0), (125, 125.0), ("12.5", 12.5)):
            with self.subTest(raw=raw):
                annotations = AnnotationValues.model_validate({"water_quality": raw})
                self.assertEqual(annotations.water_quality, expected)
                if expected is not None:
                    self.assertIsInstance(annotations.water_quality, float)

    def test_water_quality_save_requires_finite_json_number_or_null(self) -> None:
        for raw in (None, 0, 12.5):
            with self.subTest(raw=raw):
                request = SaveAnnotationRequest.model_validate({
                    "annotations": {"water_quality": raw}, "revision": "__missing__",
                })
                self.assertEqual(request.annotations.water_quality, raw)
        for raw in ("12.5", "", True, [], float("nan"), float("inf")):
            with self.subTest(raw=raw), self.assertRaisesRegex(ValueError, "water_quality"):
                SaveAnnotationRequest.model_validate({
                    "annotations": {"water_quality": raw}, "revision": "__missing__",
                })

    def test_small_frying_basket_uses_correct_spelling_only(self) -> None:
        annotations = AnnotationValues.model_validate(
            {"accessory_type": ["小炸篮"]}
        )

        self.assertEqual(annotations.accessory_type, ["小炸篮"])
        self.assertIn("小炸篮", ACCESSORY_TYPES)

        frontend = (PACKAGE_ROOT / "annotation_app" / "static" / "app.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("小炸篮", frontend)


if __name__ == "__main__":
    unittest.main()
