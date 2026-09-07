from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]


@unittest.skipIf(sys.platform == "win32" or shutil.which("bash") is None, "需要 Linux Bash")
class ShellConfigTests(unittest.TestCase):
    def test_start_uses_dotenv_conda_and_exports_allowed_roots(self) -> None:
        with tempfile.TemporaryDirectory(prefix="label shell config ") as temporary:
            root = Path(temporary)
            configured_data = root / "默认 数据"
            selected_data = root / "本次 数据"
            allowed_root = root / "允许 根目录"
            for directory in (configured_data, selected_data, allowed_root):
                directory.mkdir()

            conda_base = root / "conda root"
            fake_python = conda_base / "envs" / "label-env" / "bin" / "python"
            fake_python.parent.mkdir(parents=True)
            fake_python.write_text(
                "#!/usr/bin/env bash\n"
                "printf '%s\\0' \"$@\" > \"$CAPTURE_DIR/args\"\n"
                "printf '%s' \"$LABEL_ALLOWED_DATA_ROOTS\" > \"$CAPTURE_DIR/roots\"\n",
                encoding="utf-8",
                newline="\n",
            )
            fake_python.chmod(0o755)

            config_file = root / ".env"
            config_file.write_text(
                "\n".join(
                    (
                        f"CONDA_BASE_DIR={shlex.quote(str(conda_base))}",
                        "CONDA_ENV_NAME=label-env",
                        f"LABEL_DATA_DIR={shlex.quote(str(configured_data))}",
                        f"LABEL_ALLOWED_DATA_ROOTS={shlex.quote(str(allowed_root))}",
                        "LABEL_HOST=127.0.0.1",
                        "LABEL_PORT=9001",
                        "",
                    )
                ),
                encoding="utf-8",
                newline="\n",
            )

            environment = os.environ.copy()
            environment.update(
                {
                    "CAPTURE_DIR": str(root),
                    "ENV_FILE": str(config_file),
                }
            )
            completed = subprocess.run(
                ["bash", str(PACKAGE_ROOT / "start.sh"), str(selected_data)],
                cwd=PACKAGE_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            arguments = (root / "args").read_bytes().split(b"\0")[:-1]
            self.assertEqual(
                [item.decode() for item in arguments],
                [
                    str(PACKAGE_ROOT / "run_annotation_ui.py"),
                    "--data-dir",
                    str(selected_data),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "9001",
                    "--no-browser",
                ],
            )
            self.assertEqual(
                (root / "roots").read_text(encoding="utf-8"), str(allowed_root)
            )


if __name__ == "__main__":
    unittest.main()
