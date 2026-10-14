from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")
if BASH is None and sys.platform == "win32":
    git = shutil.which("git")
    if git:
        git_bash = Path(git).parent.parent / "bin" / "bash.exe"
        if git_bash.is_file():
            BASH = str(git_bash)


def proc_stat(state: str, start_time: int = 100) -> str:
    # Fields 3 through 22; comm deliberately contains spaces and a ')'.
    fields = [state, *("0" for _ in range(18)), str(start_time)]
    return "99999999 (annotation worker ) name) " + " ".join(fields) + "\n"


@unittest.skipIf(BASH is None, "需要 Bash")
class RestartShutdownTests(unittest.TestCase):
    def run_stop(self, scenario: str, state: str = "S") -> tuple[subprocess.CompletedProcess, list[str], bool]:
        with tempfile.TemporaryDirectory(prefix="restart shutdown ") as temporary:
            root = Path(temporary)
            (root / "10-temp_label").mkdir()
            process = root / "fake-proc" / "99999999"
            process.mkdir(parents=True)
            (process / "stat").write_text(proc_stat(state), encoding="utf-8", newline="\n")
            (root / "annotation.pid").write_text("99999999\n", encoding="utf-8")

            # Exercise the real stop block against a private /proc fixture;
            # no test signal can reach a real process and no service is started.
            script = (PACKAGE_ROOT / "restart.sh").read_text(encoding="utf-8")
            script = script.split('export ENV_FILE LABEL_DATA_DIR=', 1)[0]
            script = script.replace("/proc/", "${FIXTURE_PROC}/")
            (root / "restart.sh").write_text(script, encoding="utf-8", newline="\n")
            shim = root / "bash-env"
            shim.write_text(
                'kill() {\n'
                '  [[ "$#" == 2 && "$2" == 99999999 ]] || return 90\n'
                '  printf "%s\\n" "$1" >> "$FIXTURE_SIGNALS"\n'
                '  case "$FIXTURE_SCENARIO:$1" in\n'
                '    term_exit:-TERM|force_exit:-KILL) rm -f -- "$FIXTURE_PROC/99999999/stat" ;;\n'
                '    term_zombie:-TERM) printf "%s" "$FIXTURE_ZOMBIE_STAT" > "$FIXTURE_PROC/99999999/stat" ;;\n'
                '    reused_pid:-TERM) printf "%s" "$FIXTURE_REUSED_STAT" > "$FIXTURE_PROC/99999999/stat" ;;\n'
                '  esac\n'
                '}\n',
                encoding="utf-8", newline="\n",
            )
            environment = os.environ.copy()
            environment.update(
                {
                    "BASH_ENV": shim.as_posix(),
                    "ENV_FILE": "/nonexistent-restart-test.env",
                    "FIXTURE_PROJECT": root.as_posix(),
                    "FIXTURE_PROC": (root / "fake-proc").as_posix(),
                    "FIXTURE_SIGNALS": (root / "signals").as_posix(),
                    "FIXTURE_SCENARIO": scenario,
                    "FIXTURE_ZOMBIE_STAT": proc_stat("Z"),
                    "FIXTURE_REUSED_STAT": proc_stat("S", 200),
                    "LABEL_DATA_DIR": "10-temp_label",
                    "LABEL_PORT": "8577",
                    "WAIT_SECONDS": "1",
                    "STOP_WAIT_SECONDS": "1",
                }
            )
            completed = subprocess.run(
                [
                    BASH, "-c",
                    'cd -- "$FIXTURE_PROJECT" || exit 91\n'
                    'if [[ "$FIXTURE_SCENARIO" == unrelated ]]; then\n'
                    '  printf "%s\\0" /other/run_annotation_ui.py > "$FIXTURE_PROC/99999999/cmdline"\n'
                    'else\n'
                    '  printf "%s\\0" "$PWD/run_annotation_ui.py" > "$FIXTURE_PROC/99999999/cmdline"\n'
                    'fi\n'
                    'exec bash ./restart.sh\n',
                ],
                env=environment, capture_output=True, text=True, encoding="utf-8",
                timeout=10, check=False,
            )
            signals_file = root / "signals"
            signals = signals_file.read_text(encoding="utf-8").splitlines() if signals_file.exists() else []
            return completed, signals, (root / "annotation.pid").exists()

    def test_normal_exit_only_sends_term(self) -> None:
        result, signals, pid_exists = self.run_stop("term_exit")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(signals, ["-TERM"])
        self.assertFalse(pid_exists)

    def test_stuck_old_instance_is_killed_after_timeout(self) -> None:
        result, signals, pid_exists = self.run_stop("force_exit")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(signals, ["-TERM", "-KILL"])
        self.assertIn("SIGKILL", result.stderr)
        self.assertFalse(pid_exists)

    def test_existing_zombie_is_treated_as_exited(self) -> None:
        result, signals, pid_exists = self.run_stop("zombie", "Z")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(signals, [])
        self.assertFalse(pid_exists)

    def test_zombie_after_term_does_not_need_kill(self) -> None:
        result, signals, pid_exists = self.run_stop("term_zombie")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(signals, ["-TERM"])
        self.assertFalse(pid_exists)

    def test_reused_pid_is_not_killed(self) -> None:
        result, signals, pid_exists = self.run_stop("reused_pid")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(signals, ["-TERM"])
        self.assertFalse(pid_exists)

    def test_unrelated_process_is_not_signalled(self) -> None:
        result, signals, pid_exists = self.run_stop("unrelated")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("其他进程", result.stderr)
        self.assertEqual(signals, [])
        self.assertTrue(pid_exists)

    def test_process_still_alive_after_kill_preserves_pid_and_reports_failure(self) -> None:
        result, signals, pid_exists = self.run_stop("stuck", "D")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("SIGKILL 后仍未退出", result.stderr)
        self.assertEqual(signals, ["-TERM", "-KILL"])
        self.assertTrue(pid_exists)


if __name__ == "__main__":
    unittest.main()
