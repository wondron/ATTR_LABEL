from __future__ import annotations

import sys
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from annotation_app.config import ACCESSORY_TYPES
from annotation_app.models import AnnotationValues


class AnnotationParsingTests(unittest.TestCase):
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
