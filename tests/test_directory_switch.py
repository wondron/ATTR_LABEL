from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from fastapi.testclient import TestClient
from PIL import Image

from annotation_app.config import MISSING_REVISION
from annotation_app.main import create_app


def switch_payload(path: Path | str, generation: int = 0) -> dict[str, object]:
    return {"path": str(path), "directory_generation": generation}


def switch_directory(
    client: TestClient,
    path: Path | str,
    generation: int = 0,
):
    return client.post(
        "/api/v1/data-directory/select",
        headers={"X-Requested-With": "annotation-ui"},
        json=switch_payload(path, generation),
    )


def browse_directories(
    client: TestClient,
    *,
    path: Path | str | None = None,
    generation: int = 0,
    with_header: bool = True,
):
    params: dict[str, object] = {"directory_generation": generation}
    if path is not None:
        params["path"] = str(path)
    headers = {"X-Requested-With": "annotation-ui"} if with_header else {}
    return client.get(
        "/api/v1/data-directories",
        headers=headers,
        params=params,
    )


class LinuxDirectorySwitchTests(unittest.TestCase):
    def test_switch_moves_io_and_same_directory_keeps_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            old_dir = root / "旧目录"
            new_dir = root / "新 目录"
            old_dir.mkdir()
            new_dir.mkdir()
            Image.new("RGB", (8, 8), "red").save(old_dir / "old.jpg")
            Image.new("RGB", (8, 8), "blue").save(new_dir / "new.jpg")

            with TestClient(
                create_app(old_dir, allowed_data_roots=(root,))
            ) as client:
                response = switch_directory(client, new_dir)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertTrue(response.json()["changed"])
                self.assertEqual(response.json()["directory_generation"], 1)
                self.assertEqual(response.json()["data_dir"], new_dir.as_posix())

                images = client.get(
                    "/api/v1/images",
                    params={"directory_generation": 1},
                )
                self.assertEqual(images.status_code, 200, images.text)
                self.assertEqual(
                    [item["image_id"] for item in images.json()["items"]],
                    ["new.jpg"],
                )

                saved = client.put(
                    "/api/v1/annotation",
                    params={
                        "directory_generation": 1,
                        "image_id": "new.jpg",
                    },
                    json={
                        "revision": MISSING_REVISION,
                        "annotations": {
                            "food_name": "测试",
                            "food_count": 1,
                            "quality": 100.0,
                            "device_model": None,
                            "container_type": ["无"],
                            "accessory_type": ["无"],
                            "rack_level": ["无"],
                            "food_size": None,
                        },
                    },
                )
                self.assertEqual(saved.status_code, 200, saved.text)
                self.assertTrue((new_dir / "new.json").is_file())
                self.assertFalse((old_dir / "new.json").exists())

                same = switch_directory(client, new_dir, generation=1)
                self.assertEqual(same.status_code, 200, same.text)
                self.assertFalse(same.json()["changed"])
                self.assertEqual(same.json()["directory_generation"], 1)

    def test_invalid_and_disallowed_paths_do_not_change_repository(self) -> None:
        with (
            tempfile.TemporaryDirectory() as allowed_temporary,
            tempfile.TemporaryDirectory() as outside_temporary,
        ):
            allowed_root = Path(allowed_temporary).resolve()
            current = allowed_root / "current"
            current.mkdir()
            Image.new("RGB", (8, 8), "red").save(current / "keep.jpg")
            outside = Path(outside_temporary).resolve()

            with TestClient(
                create_app(current, allowed_data_roots=(allowed_root,))
            ) as client:
                missing_header = client.post(
                    "/api/v1/data-directory/select",
                    json=switch_payload(current),
                )
                self.assertEqual(missing_header.status_code, 403)

                old_frontend = client.post(
                    "/api/v1/data-directory/select",
                    headers={"X-Requested-With": "annotation-ui"},
                )
                self.assertEqual(old_frontend.status_code, 409)
                self.assertEqual(
                    old_frontend.json()["detail"]["code"],
                    "frontend_version_mismatch",
                )
                self.assertIn("Ctrl+F5", old_frontend.json()["detail"]["message"])

                relative = switch_directory(client, "relative/path")
                self.assertEqual(relative.status_code, 400)
                self.assertEqual(
                    relative.json()["detail"]["code"],
                    "absolute_path_required",
                )

                missing = switch_directory(client, allowed_root / "missing")
                self.assertEqual(missing.status_code, 400)
                self.assertEqual(
                    missing.json()["detail"]["code"],
                    "invalid_data_directory",
                )

                disallowed = switch_directory(client, outside)
                self.assertEqual(disallowed.status_code, 403)
                self.assertEqual(
                    disallowed.json()["detail"]["code"],
                    "data_directory_not_allowed",
                )

                config = client.get("/api/v1/config").json()
                self.assertEqual(config["data_dir"], current.as_posix())
                self.assertEqual(config["directory_generation"], 0)

    def test_stale_page_cannot_switch_again(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            directories = [root / name for name in ("a", "b", "c")]
            for directory in directories:
                directory.mkdir()

            with TestClient(
                create_app(directories[0], allowed_data_roots=(root,))
            ) as client:
                first = switch_directory(client, directories[1], generation=0)
                self.assertEqual(first.status_code, 200, first.text)

                stale = switch_directory(client, directories[2], generation=0)
                self.assertEqual(stale.status_code, 409, stale.text)
                self.assertEqual(
                    stale.json()["detail"]["code"],
                    "data_directory_changed",
                )
                config = client.get("/api/v1/config").json()
                self.assertEqual(config["data_dir"], directories[1].as_posix())
                self.assertEqual(config["directory_generation"], 1)

    def test_frontend_contains_server_path_dialog(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with TestClient(create_app(temporary)) as client:
                html = client.get("/").text
                script_response = client.get("/static/app.js")
                script = script_response.text

                self.assertEqual(client.get("/").headers["cache-control"], "no-store, max-age=0")
                self.assertEqual(script_response.headers["cache-control"], "no-store, max-age=0")

        for element_id in (
            "dataDirectoryDialog",
            "dataDirectoryForm",
            "dataDirectoryPathInput",
            "dataDirectoryError",
            "confirmDataDirectoryButton",
        ):
            self.assertIn(f'id="{element_id}"', html)
        self.assertIn('apiRequest("/data-directory/select"', script)
        self.assertIn("directory_generation: state.directorySwitchGeneration", script)
        self.assertNotIn("webkitdirectory", html + script)
        self.assertIn("app.js?v=linux-realtime-stats-2", html)

    def test_empty_allowed_roots_are_restricted_to_initial_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            current = root / "当前任务"
            sibling = root / "其他任务"
            current.mkdir()
            sibling.mkdir()

            with TestClient(
                create_app(current, allowed_data_roots=())
            ) as client:
                config = client.get("/api/v1/config").json()
                self.assertEqual(
                    config["allowed_data_roots"],
                    [current.as_posix()],
                )

                roots = browse_directories(client)
                self.assertEqual(roots.status_code, 200, roots.text)
                self.assertEqual(
                    roots.json()["directories"],
                    [{"name": current.name, "path": current.as_posix()}],
                )

                rejected = switch_directory(client, sibling)
                self.assertEqual(rejected.status_code, 403, rejected.text)
                self.assertEqual(
                    rejected.json()["detail"]["code"],
                    "data_directory_not_allowed",
                )


class LinuxDirectoryBrowserTests(unittest.TestCase):
    def test_roots_and_nested_navigation_preserve_linux_names(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            first_root = base / "允许 根一"
            second_root = base / "允许根二"
            current = first_root / "当前任务"
            chinese_directory = first_root / "中文 子目录"
            nested = chinese_directory / "第二 层"
            leaf = nested / "叶子目录"
            for directory in (
                current,
                nested,
                leaf,
                second_root,
            ):
                directory.mkdir(parents=True, exist_ok=True)

            with TestClient(
                create_app(
                    current,
                    allowed_data_roots=(first_root, second_root),
                )
            ) as client:
                roots = browse_directories(client)
                self.assertEqual(roots.status_code, 200, roots.text)
                roots_payload = roots.json()
                self.assertEqual(roots_payload["mode"], "roots")
                self.assertIsNone(roots_payload["current_path"])
                self.assertIsNone(roots_payload["parent_path"])
                self.assertEqual(roots_payload["breadcrumbs"], [])
                self.assertEqual(roots_payload["directory_generation"], 0)
                self.assertEqual(
                    roots_payload["allowed_data_roots"],
                    [first_root.as_posix(), second_root.as_posix()],
                )
                self.assertEqual(
                    {
                        (item["name"], item["path"])
                        for item in roots_payload["directories"]
                    },
                    {
                        (first_root.name, first_root.as_posix()),
                        (second_root.name, second_root.as_posix()),
                    },
                )

                root_listing = browse_directories(client, path=first_root)
                self.assertEqual(
                    root_listing.status_code,
                    200,
                    root_listing.text,
                )
                root_payload = root_listing.json()
                self.assertEqual(root_payload["mode"], "directory")
                self.assertEqual(
                    root_payload["current_path"], first_root.as_posix()
                )
                self.assertIsNone(root_payload["parent_path"])
                self.assertEqual(
                    root_payload["breadcrumbs"],
                    [{"name": first_root.name, "path": first_root.as_posix()}],
                )
                self.assertEqual(
                    [item["name"] for item in root_payload["directories"]],
                    sorted(
                        [current.name, chinese_directory.name],
                        key=str.casefold,
                    ),
                )

                nested_listing = browse_directories(client, path=nested)
                self.assertEqual(
                    nested_listing.status_code,
                    200,
                    nested_listing.text,
                )
                nested_payload = nested_listing.json()
                self.assertEqual(
                    nested_payload["current_path"], nested.as_posix()
                )
                self.assertEqual(
                    nested_payload["parent_path"],
                    chinese_directory.as_posix(),
                )
                self.assertEqual(
                    nested_payload["breadcrumbs"],
                    [
                        {
                            "name": first_root.name,
                            "path": first_root.as_posix(),
                        },
                        {
                            "name": chinese_directory.name,
                            "path": chinese_directory.as_posix(),
                        },
                        {"name": nested.name, "path": nested.as_posix()},
                    ],
                )
                self.assertEqual(
                    nested_payload["directories"],
                    [{"name": leaf.name, "path": leaf.as_posix()}],
                )

    def test_browse_rejects_unauthorised_stale_and_unsafe_paths(self) -> None:
        with (
            tempfile.TemporaryDirectory() as allowed_temporary,
            tempfile.TemporaryDirectory() as outside_temporary,
        ):
            allowed_root = Path(allowed_temporary).resolve()
            current = allowed_root / "current"
            current.mkdir()
            file_path = allowed_root / "不是目录.txt"
            file_path.write_text("test", encoding="utf-8")
            missing_path = allowed_root / "不存在"
            outside = Path(outside_temporary).resolve()

            with TestClient(
                create_app(current, allowed_data_roots=(allowed_root,))
            ) as client:
                cases = (
                    (
                        "missing header",
                        browse_directories(
                            client,
                            path=allowed_root,
                            with_header=False,
                        ),
                        403,
                        "directory_browse_forbidden",
                    ),
                    (
                        "stale generation",
                        browse_directories(
                            client,
                            path=allowed_root,
                            generation=1,
                        ),
                        409,
                        "data_directory_changed",
                    ),
                    (
                        "relative path",
                        browse_directories(client, path="relative/path"),
                        400,
                        "absolute_path_required",
                    ),
                    (
                        "missing path",
                        browse_directories(client, path=missing_path),
                        404,
                        "data_directory_not_found",
                    ),
                    (
                        "regular file",
                        browse_directories(client, path=file_path),
                        400,
                        "not_a_data_directory",
                    ),
                    (
                        "outside allowed roots",
                        browse_directories(client, path=outside),
                        403,
                        "data_directory_not_allowed",
                    ),
                    (
                        "parent traversal outside allowed root",
                        browse_directories(
                            client,
                            path=allowed_root / "..",
                        ),
                        403,
                        "data_directory_not_allowed",
                    ),
                )
                for label, response, status_code, error_code in cases:
                    with self.subTest(label=label):
                        self.assertEqual(
                            response.status_code,
                            status_code,
                            response.text,
                        )
                        self.assertEqual(
                            response.json()["detail"]["code"], error_code
                        )

                config = client.get("/api/v1/config").json()
                self.assertEqual(config["data_dir"], current.as_posix())
                self.assertEqual(config["directory_generation"], 0)

    def test_symlink_cannot_escape_allowed_root(self) -> None:
        with (
            tempfile.TemporaryDirectory() as allowed_temporary,
            tempfile.TemporaryDirectory() as outside_temporary,
        ):
            allowed_root = Path(allowed_temporary).resolve()
            current = allowed_root / "current"
            current.mkdir()
            outside = Path(outside_temporary).resolve()
            escape_link = allowed_root / "越界链接"
            try:
                escape_link.symlink_to(outside, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"当前平台不能创建目录软链接：{exc}")

            with TestClient(
                create_app(current, allowed_data_roots=(allowed_root,))
            ) as client:
                listing = browse_directories(client, path=allowed_root)
                self.assertEqual(listing.status_code, 200, listing.text)
                listed_paths = {
                    item["path"] for item in listing.json()["directories"]
                }
                self.assertNotIn(escape_link.as_posix(), listed_paths)
                self.assertNotIn(outside.as_posix(), listed_paths)

                escaped = browse_directories(client, path=escape_link)
                self.assertEqual(escaped.status_code, 403, escaped.text)
                self.assertEqual(
                    escaped.json()["detail"]["code"],
                    "data_directory_not_allowed",
                )

    def test_frontend_contains_clickable_server_directory_browser(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with TestClient(create_app(temporary)) as client:
                html = client.get("/").text
                script = client.get("/static/app.js").text

        for element_id in (
            "dataDirectoryDialog",
            "dataDirectoryForm",
            "dataDirectoryPathInput",
            "dataDirectoryGoButton",
            "dataDirectoryUpButton",
            "dataDirectoryRefreshButton",
            "dataDirectoryBreadcrumbs",
            "dataDirectoryList",
            "dataDirectoryLoading",
            "dataDirectoryEmpty",
            "selectedDataDirectoryText",
            "allowedDataRootsText",
            "dataDirectoryError",
            "cancelDataDirectoryButton",
            "cancelDataDirectoryIcon",
            "confirmDataDirectoryButton",
            "confirmDataDirectoryButtonText",
        ):
            self.assertIn(f'id="{element_id}"', html)

        for function_name in (
            "openDataDirectoryDialog",
            "browseDataDirectories",
            "browseEnteredDataDirectory",
            "renderDirectoryBreadcrumbs",
            "renderDirectoryList",
            "selectDataDirectory",
            "chooseDataDirectory",
        ):
            self.assertIn(f"function {function_name}", script)

        self.assertIn('apiRequest(`/data-directories?', script)
        self.assertIn('params.set("directory_generation"', script)
        self.assertIn('headers: { "X-Requested-With": "annotation-ui" }', script)
        self.assertIn('button.className = "directory-browser-item"', script)
        self.assertIn("button.dataset.path", script)
        self.assertIn('button.addEventListener("click"', script)
        self.assertIn('button.addEventListener("dblclick"', script)
        self.assertIn('className = "directory-breadcrumb"', script)
        self.assertIn("state.directoryBrowseParentPath", script)
        self.assertIn("state.directorySelectedPath", script)
        self.assertNotIn("webkitdirectory", html + script)


if __name__ == "__main__":
    unittest.main()
