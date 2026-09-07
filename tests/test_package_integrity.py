from __future__ import annotations

import sys
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))


class LinuxPackageIntegrityTests(unittest.TestCase):
    def test_runtime_files_and_dependencies_are_complete(self) -> None:
        required_files = (
            "start.sh",
            "restart.sh",
            "run_annotation_ui.py",
            "verify_package.py",
            "annotation_app/main.py",
            "annotation_app/statistics.py",
            "annotation_app/watcher.py",
            "annotation_app/static/index.html",
            "annotation_app/static/app.js",
            "annotation_app/static/styles.css",
            "annotation_app/static/directory-browser.css",
        )
        missing = [name for name in required_files if not (PACKAGE_ROOT / name).is_file()]
        self.assertEqual(missing, [])

        requirements = (PACKAGE_ROOT / "requirements.txt").read_text(
            encoding="utf-8"
        )
        self.assertIn("watchdog>=", requirements)
        self.assertNotIn("requests", requirements)

    def test_shell_scripts_are_linux_ready_for_nohup(self) -> None:
        for name in ("start.sh", "restart.sh"):
            raw = (PACKAGE_ROOT / name).read_bytes()
            self.assertTrue(raw.startswith(b"#!/usr/bin/env bash\n"))
            self.assertNotIn(b"\r\n", raw)

        restart = (PACKAGE_ROOT / "restart.sh").read_text(encoding="utf-8")
        start = (PACKAGE_ROOT / "start.sh").read_text(encoding="utf-8")
        launcher = (PACKAGE_ROOT / "run_annotation_ui.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("nohup /usr/bin/env bash", restart)
        self.assertIn("annotation.pid", restart)
        self.assertIn("annotation.log", restart)
        self.assertIn("HEALTH_DEADLINE=$((SECONDS + WAIT_SECONDS))", restart)
        self.assertIn("curl 错误：${HEALTH_ERROR}", restart)
        self.assertIn("/root/miniconda3/envs/wondron/bin/python3", restart)
        self.assertIn('LABEL_HOST="${LABEL_HOST:-0.0.0.0}"', restart)
        self.assertIn('LABEL_PORT="${LABEL_PORT:-8577}"', restart)
        self.assertIn('HOST="${LABEL_HOST:-0.0.0.0}"', start)
        self.assertIn('PORT="${LABEL_PORT:-8577}"', start)
        self.assertIn('DEFAULT_HOST = "0.0.0.0"', launcher)
        self.assertIn("DEFAULT_PORT = 8577", launcher)
        self.assertIn("wait_for_initial_scan=False", launcher)

    def test_readme_documents_nohup_and_remote_access(self) -> None:
        readme = (PACKAGE_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("chmod +x start.sh restart.sh", readme)
        self.assertIn("./restart.sh", readme)
        self.assertIn("http://118.31.105.171:8577/", readme)
        self.assertIn("LABEL_HOST=0.0.0.0", readme)
        self.assertIn("LABEL_PORT=8577", readme)
        self.assertIn("annotation.pid", readme)
        self.assertIn("annotation.log", readme)
        self.assertIn(
            "LABEL_ALLOWED_DATA_ROOTS=/models/wangzhuo/01-code/03_deploy",
            readme,
        )
        self.assertNotIn("tee /etc/systemd/system", readme)
        self.assertNotIn("空（不限制）", readme)


if __name__ == "__main__":
    unittest.main()
