"""FastAPI 应用工厂与本地标注 API。"""

from __future__ import annotations

import asyncio
import json
import mimetypes
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Literal, NoReturn
from urllib.parse import quote

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .config import (
    ANNOTATION_VERSION,
    DEFAULT_DATA_DIR,
    FIELD_DEFINITIONS,
    STATIC_DIR,
)
from .models import SaveAnnotationRequest, SelectDataDirectoryRequest
from .repository import (
    AnnotationRepository,
    ImageNotFoundError,
    InvalidAnnotationFileError,
    InvalidImageIdError,
    RepositoryError,
    RevisionConflictError,
)
from .statistics import build_attribute_statistics
from .watcher import DirectoryChange, DirectorySyncService


def _resolve_allowed_data_roots(values: list[str | Path]) -> tuple[Path, ...]:
    roots: list[Path] = []
    for raw_value in values:
        value = str(raw_value).strip()
        if not value:
            continue
        candidate = Path(value)
        if not candidate.is_absolute():
            raise ValueError(
                f"允许的数据根路径必须是绝对路径：{value}"
            )
        resolved = candidate.resolve(strict=True)
        if not resolved.is_dir():
            raise NotADirectoryError(f"允许的数据根路径不是文件夹：{resolved}")
        if not os.access(resolved, os.R_OK | os.X_OK):
            raise PermissionError(f"允许的数据根路径不可读取或进入：{resolved}")
        if resolved not in roots:
            roots.append(resolved)
    return tuple(roots)


def _configured_allowed_data_roots() -> tuple[Path, ...]:
    """读取可由网页切换到的数据根目录；空值由应用收紧到初始目录。"""
    raw = os.environ.get("LABEL_ALLOWED_DATA_ROOTS", "")
    return _resolve_allowed_data_roots(raw.split(os.pathsep))


def _path_is_within(candidate: Path, roots: tuple[Path, ...]) -> bool:
    return not roots or any(
        candidate == root or candidate.is_relative_to(root)
        for root in roots
    )


def _directory_display_name(path: Path) -> str:
    """生成适合文件夹选择器显示的名称，兼容 ``/`` 和盘符根目录。"""
    return path.name or path.as_posix()


def _containing_browse_root(
    candidate: Path,
    browse_roots: tuple[Path, ...],
) -> Path | None:
    """返回包含 candidate 的最具体浏览根目录。"""
    containing = (
        root
        for root in browse_roots
        if candidate == root or candidate.is_relative_to(root)
    )
    return max(containing, key=lambda root: len(root.parts), default=None)


def _directory_breadcrumbs(candidate: Path, root: Path) -> list[dict[str, str]]:
    """构造从允许根目录到当前目录的面包屑。"""
    relative = candidate.relative_to(root)
    paths = [root]
    current = root
    for part in relative.parts:
        current = current / part
        paths.append(current)
    return [
        {"name": _directory_display_name(path), "path": path.as_posix()}
        for path in paths
    ]


def _open_browse_directory(
    requested_path: str,
    browse_roots: tuple[Path, ...],
) -> tuple[Path, Path]:
    """解析并校验一个可浏览目录，所有权限判断均基于真实路径。"""
    raw_path = requested_path.strip()
    if not raw_path or any(ord(character) < 32 for character in raw_path):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_data_directory_path",
                "message": "文件夹路径不能为空，也不能包含控制字符。",
            },
        )

    candidate = Path(raw_path)
    if not candidate.is_absolute():
        raise HTTPException(
            status_code=400,
            detail={
                "code": "absolute_path_required",
                "message": "请选择 Linux 服务器上的绝对目录路径（以 / 开头）。",
            },
        )

    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "data_directory_not_found",
                "message": "所选文件夹不存在或已经被移除。",
            },
        ) from exc
    except NotADirectoryError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "not_a_data_directory",
                "message": "所选路径不是文件夹。",
            },
        ) from exc
    except PermissionError as exc:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "data_directory_unreadable",
                "message": "服务用户没有权限读取或进入所选文件夹。",
            },
        ) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_data_directory",
                "message": f"无法打开所选文件夹：{exc}",
            },
        ) from exc

    if not resolved.is_dir():
        raise HTTPException(
            status_code=400,
            detail={
                "code": "not_a_data_directory",
                "message": "所选路径不是文件夹。",
            },
        )

    root = _containing_browse_root(resolved, browse_roots)
    if root is None:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "data_directory_not_allowed",
                "message": "该文件夹不在服务允许浏览的数据根目录内。",
                "allowed_data_roots": [
                    browse_root.as_posix() for browse_root in browse_roots
                ],
            },
        )

    if not os.access(resolved, os.R_OK | os.X_OK):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "data_directory_unreadable",
                "message": "服务用户没有权限读取或进入所选文件夹。",
            },
        )
    return resolved, root


def _list_browse_directories(
    directory: Path,
    browse_roots: tuple[Path, ...],
) -> list[dict[str, str]]:
    """仅列出安全、可进入且真实路径未越过允许根目录的直接子目录。"""
    directories: list[dict[str, str]] = []
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                try:
                    if not entry.is_dir(follow_symlinks=True):
                        continue
                    resolved = Path(entry.path).resolve(strict=True)
                    if _containing_browse_root(resolved, browse_roots) is None:
                        # 目录内指向允许范围外的软链接不能出现在选择器中。
                        continue
                    if not os.access(resolved, os.R_OK | os.X_OK):
                        continue
                    directories.append(
                        {"name": entry.name, "path": resolved.as_posix()}
                    )
                except (OSError, RuntimeError):
                    # 单个损坏、循环或无权限软链接不应影响其他目录的浏览。
                    continue
    except PermissionError as exc:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "data_directory_unreadable",
                "message": "服务用户没有权限列出所选文件夹。",
            },
        ) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_data_directory",
                "message": f"无法列出所选文件夹：{exc}",
            },
        ) from exc

    return sorted(
        directories,
        key=lambda item: (item["name"].casefold(), item["path"]),
    )


def _raise_repository_http(exc: RepositoryError) -> NoReturn:
    if isinstance(exc, InvalidImageIdError):
        status_code = 400
        code = "invalid_image_id"
    elif isinstance(exc, ImageNotFoundError):
        status_code = 404
        code = "image_not_found"
    elif isinstance(exc, InvalidAnnotationFileError):
        status_code = 422
        code = "invalid_annotation_file"
    elif isinstance(exc, RevisionConflictError):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "revision_conflict",
                "message": str(exc),
                "current_revision": exc.current_revision,
            },
        ) from exc
    else:
        status_code = 500
        code = "repository_error"

    raise HTTPException(
        status_code=status_code,
        detail={"code": code, "message": str(exc)},
    ) from exc


def create_app(
    data_dir: str | Path | None = None,
    *,
    allowed_data_roots: tuple[str | Path, ...] | None = None,
) -> FastAPI:
    repository = AnnotationRepository(data_dir or DEFAULT_DATA_DIR)
    if allowed_data_roots is None:
        resolved_allowed_roots = _configured_allowed_data_roots()
    else:
        resolved_allowed_roots = _resolve_allowed_data_roots(
            list(allowed_data_roots)
        )
    effective_allowed_roots = resolved_allowed_roots or (repository.data_dir,)
    directory_sync = DirectorySyncService()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        await directory_sync.start(
            repository.data_dir,
            application.state.directory_generation,
        )
        try:
            yield
        finally:
            await directory_sync.stop()

    app = FastAPI(
        title="多属性图像标注工具",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.repository = repository
    app.state.image_locks = {}
    app.state.directory_switch_lock = asyncio.Lock()
    app.state.directory_generation = 0
    app.state.allowed_data_roots = effective_allowed_roots
    app.state.directory_sync = directory_sync

    @app.middleware("http")
    async def disable_ui_asset_cache(
        request: Request,
        call_next: Any,
    ) -> Response:
        """避免升级后浏览器继续运行与后端不兼容的旧前端文件。"""
        response = await call_next(request)
        if (
            request.url.path == "/"
            or request.url.path.startswith("/static/")
            or request.url.path == "/api/v1/data-directories"
        ):
            response.headers["Cache-Control"] = "no-store, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    def image_lock(image_id: str) -> asyncio.Lock:
        lock = app.state.image_locks.get(image_id)
        if lock is None:
            lock = asyncio.Lock()
            app.state.image_locks[image_id] = lock
        return lock

    def repository_for_generation(
        requested_generation: int | None,
    ) -> tuple[AnnotationRepository, int]:
        current_generation = app.state.directory_generation
        current_repository = repository
        if requested_generation != current_generation:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "data_directory_changed",
                    "message": (
                        "数据目录已在其他页面中切换。当前页面不会继续读取或保存，"
                        "请重新选择文件夹或刷新页面。"
                    ),
                    "current_directory_generation": current_generation,
                    "data_dir": current_repository.data_dir.as_posix(),
                },
            )
        return current_repository, current_generation

    @app.get("/api/v1/config")
    async def get_config() -> dict[str, Any]:
        current_repository = repository
        current_generation = app.state.directory_generation
        return {
            "annotation_version": ANNOTATION_VERSION,
            "data_dir": current_repository.data_dir.as_posix(),
            "allowed_data_roots": [
                root.as_posix() for root in effective_allowed_roots
            ],
            "directory_generation": current_generation,
            "fields": list(FIELD_DEFINITIONS),
        }

    @app.get("/api/v1/data-directories")
    async def browse_data_directories(
        path: str | None = Query(default=None, max_length=4096),
        directory_generation: int = Query(ge=0),
        x_requested_with: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """为网页文件夹选择器返回服务器上的直接子目录。"""
        if x_requested_with != "annotation-ui":
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "directory_browse_forbidden",
                    "message": "服务器文件夹只能由标注页面浏览。",
                },
            )

        current_repository, current_generation = repository_for_generation(
            directory_generation
        )
        browse_roots = effective_allowed_roots
        root_items = [
            {
                "name": _directory_display_name(root),
                "path": root.as_posix(),
            }
            for root in browse_roots
        ]

        if path is None or not path.strip():
            return {
                "mode": "roots",
                "current_path": None,
                "parent_path": None,
                "breadcrumbs": [],
                "directories": root_items,
                "allowed_data_roots": [
                    root.as_posix() for root in browse_roots
                ],
                "directory_generation": current_generation,
            }

        directory, browse_root = await asyncio.to_thread(
            _open_browse_directory,
            path,
            browse_roots,
        )
        directories = await asyncio.to_thread(
            _list_browse_directories,
            directory,
            browse_roots,
        )
        parent_path = (
            directory.parent.as_posix()
            if directory != browse_root
            else None
        )
        return {
            "mode": "directory",
            "current_path": directory.as_posix(),
            "parent_path": parent_path,
            "breadcrumbs": _directory_breadcrumbs(directory, browse_root),
            "directories": directories,
            "allowed_data_roots": [root.as_posix() for root in browse_roots],
            "directory_generation": current_generation,
        }

    @app.post("/api/v1/data-directory/select")
    async def select_data_directory(
        request: SelectDataDirectoryRequest | None = None,
        x_requested_with: str | None = Header(default=None),
    ) -> dict[str, Any]:
        nonlocal repository
        if x_requested_with != "annotation-ui":
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "directory_switch_forbidden",
                    "message": "数据目录只能由标注页面切换。",
                },
            )
        if request is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "frontend_version_mismatch",
                    "message": (
                        "浏览器仍在使用旧版页面资源。请按 Ctrl+F5 强制刷新后，"
                        "重新点击“切换文件夹”。"
                    ),
                },
            )
        if app.state.directory_switch_lock.locked():
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "directory_switch_busy",
                    "message": "另一个数据目录切换正在进行，请稍后重试。",
                },
            )
        async with app.state.directory_switch_lock:
            current_repository = repository
            current_generation = app.state.directory_generation
            if request.directory_generation != current_generation:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "data_directory_changed",
                        "message": (
                            "数据目录已在其他页面中切换，请刷新当前页面后重试。"
                        ),
                        "current_directory_generation": current_generation,
                        "data_dir": current_repository.data_dir.as_posix(),
                    },
                )

            requested_path = Path(request.path)
            if not requested_path.is_absolute():
                raise HTTPException(
                    status_code=400,
                    detail={
                        "code": "absolute_path_required",
                        "message": "请输入 Linux 服务器上的绝对目录路径（以 / 开头）。",
                    },
                )

            try:
                candidate = await asyncio.to_thread(
                    AnnotationRepository.open_existing, requested_path
                )
                if not _path_is_within(
                    candidate.data_dir, effective_allowed_roots
                ):
                    raise HTTPException(
                        status_code=403,
                        detail={
                            "code": "data_directory_not_allowed",
                            "message": (
                                "该目录不在服务允许切换的数据根目录内。"
                            ),
                            "allowed_data_roots": [
                                root.as_posix()
                                for root in effective_allowed_roots
                            ],
                        },
                    )
                await asyncio.to_thread(candidate.list_images)
            except HTTPException:
                raise
            except (OSError, TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "code": "invalid_data_directory",
                        "message": f"无法使用所选文件夹：{exc}",
                    },
                ) from exc

            changed = candidate.data_dir != current_repository.data_dir
            if changed:
                repository = candidate
                app.state.repository = candidate
                app.state.image_locks = {}
                app.state.directory_generation += 1
                await directory_sync.switch_directory(
                    candidate.data_dir,
                    app.state.directory_generation,
                )
            return {
                "status": "selected",
                "changed": changed,
                "data_dir": candidate.data_dir.as_posix(),
                "directory_generation": app.state.directory_generation,
            }

    @app.get("/api/v1/images")
    async def get_images(
        status: Literal["all", "labeled", "unlabeled", "invalid"] = "all",
        search: str = "",
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=100000, ge=1, le=100000),
        directory_generation: int | None = Query(default=None, ge=0),
    ) -> dict[str, Any]:
        current_repository, current_generation = repository_for_generation(
            directory_generation
        )
        try:
            all_items = await asyncio.to_thread(current_repository.list_images)
        except RepositoryError as exc:
            _raise_repository_http(exc)

        summary = {
            "total": len(all_items),
            "labeled": sum(bool(item["annotation_exists"]) for item in all_items),
            "unlabeled": sum(not item["annotation_exists"] for item in all_items),
            "invalid": sum(item["annotation_valid"] is False for item in all_items),
        }

        filtered = all_items
        if status == "labeled":
            filtered = [item for item in filtered if item["annotation_exists"]]
        elif status == "unlabeled":
            filtered = [item for item in filtered if not item["annotation_exists"]]
        elif status == "invalid":
            filtered = [item for item in filtered if item["annotation_valid"] is False]

        normalized_search = search.strip().casefold()
        if normalized_search:
            filtered = [
                item
                for item in filtered
                if normalized_search in item["image_id"].casefold()
            ]

        total_filtered = len(filtered)
        page = filtered[offset : offset + limit]
        for item in page:
            item["image_url"] = (
                "/api/v1/image?image_id="
                + quote(item["image_id"], safe="")
                + f"&directory_generation={current_generation}"
            )

        return {
            "items": page,
            "offset": offset,
            "limit": limit,
            "filtered_total": total_filtered,
            "summary": summary,
            "directory_generation": current_generation,
        }

    @app.get("/api/v1/image-events")
    async def get_image_events(
        request: Request,
        directory_generation: int = Query(ge=0),
    ) -> StreamingResponse:
        """Stream settled image-directory changes for the selected directory."""

        _, current_generation = repository_for_generation(directory_generation)
        subscriber_id, event_queue = directory_sync.subscribe(current_generation)

        def encode_event(
            event_name: str,
            payload: dict[str, Any],
            event_id: int | None = None,
        ) -> str:
            lines: list[str] = []
            if event_id is not None:
                lines.append(f"id: {event_id}")
            lines.append(f"event: {event_name}")
            lines.append(
                "data: "
                + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            )
            return "\n".join(lines) + "\n\n"

        async def stream_events() -> AsyncIterator[str]:
            try:
                yield "retry: 2000\n" + encode_event(
                    "ready",
                    {
                        "type": "ready",
                        "sequence": directory_sync.current_sequence,
                        "directory_generation": current_generation,
                    },
                )
                while True:
                    try:
                        change: DirectoryChange = await asyncio.wait_for(
                            event_queue.get(), timeout=15.0
                        )
                    except asyncio.TimeoutError:
                        if await request.is_disconnected():
                            break
                        yield encode_event(
                            "heartbeat",
                            {
                                "type": "heartbeat",
                                "directory_generation": current_generation,
                                "timestamp": datetime.now(timezone.utc)
                                .isoformat(timespec="seconds")
                                .replace("+00:00", "Z"),
                            },
                        )
                        continue

                    event_name = {
                        "created": "image-created",
                        "modified": "image-modified",
                        "moved": "image-moved",
                        "deleted": "image-deleted",
                    }.get(change.kind, change.kind)
                    yield encode_event(
                        event_name,
                        change.as_dict(),
                        event_id=change.sequence,
                    )
                    if change.kind == "directory-changed":
                        break
            finally:
                directory_sync.unsubscribe(subscriber_id)

        return StreamingResponse(
            stream_events(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/v1/statistics")
    async def get_statistics(
        directory_generation: int | None = Query(default=None, ge=0),
    ) -> dict[str, Any]:
        current_repository, current_generation = repository_for_generation(
            directory_generation
        )

        def collect_statistics() -> dict[str, Any]:
            summary, documents = current_repository.collect_annotation_documents()
            return {
                "summary": summary,
                "fields": build_attribute_statistics(
                    documents, FIELD_DEFINITIONS
                ),
            }

        try:
            payload = await asyncio.to_thread(collect_statistics)
        except RepositoryError as exc:
            _raise_repository_http(exc)

        payload["directory_generation"] = current_generation
        return payload

    @app.get("/api/v1/image")
    async def get_image(
        image_id: str = Query(min_length=1),
        directory_generation: int | None = Query(default=None, ge=0),
    ) -> FileResponse:
        current_repository, _ = repository_for_generation(directory_generation)
        try:
            image_path = current_repository.resolve_image(image_id)
        except RepositoryError as exc:
            _raise_repository_http(exc)

        media_type, _ = mimetypes.guess_type(str(image_path))
        return FileResponse(
            path=image_path,
            media_type=media_type or "application/octet-stream",
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/api/v1/annotation")
    async def get_annotation(
        image_id: str = Query(min_length=1),
        directory_generation: int | None = Query(default=None, ge=0),
    ) -> dict[str, Any]:
        current_repository, current_generation = repository_for_generation(
            directory_generation
        )
        try:
            document, revision = await asyncio.to_thread(
                current_repository.read_annotation, image_id
            )
        except InvalidAnnotationFileError as exc:
            try:
                current_revision = await asyncio.to_thread(
                    current_repository.current_revision, image_id
                )
            except RepositoryError:
                current_revision = None
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "invalid_annotation_file",
                    "message": str(exc),
                    "current_revision": current_revision,
                },
            ) from exc
        except RepositoryError as exc:
            _raise_repository_http(exc)

        return {
            "exists": document is not None,
            "annotation": document.model_dump(mode="json") if document else None,
            "revision": revision,
            "directory_generation": current_generation,
        }

    @app.put("/api/v1/annotation")
    async def put_annotation(
        request: SaveAnnotationRequest,
        image_id: str = Query(min_length=1),
        directory_generation: int | None = Query(default=None, ge=0),
    ) -> dict[str, Any]:
        current_repository, current_generation = repository_for_generation(
            directory_generation
        )
        try:
            image_path = current_repository.resolve_image(image_id)
            canonical_id = current_repository.image_id_for_path(image_path)
        except RepositoryError as exc:
            _raise_repository_http(exc)

        async with image_lock(canonical_id):
            try:
                document, revision = await asyncio.to_thread(
                    current_repository.save_annotation,
                    canonical_id,
                    request.annotations,
                    request.revision,
                )
            except RepositoryError as exc:
                _raise_repository_http(exc)

        return {
            "exists": True,
            "annotation": document.model_dump(mode="json"),
            "revision": revision,
            "directory_generation": current_generation,
        }

    @app.get("/api/v1/health")
    async def get_health() -> dict[str, Any]:
        current_repository = repository
        current_generation = app.state.directory_generation
        exists = current_repository.data_dir.is_dir()
        readable = os.access(current_repository.data_dir, os.R_OK)
        writable = os.access(current_repository.data_dir, os.W_OK)
        healthy = exists and readable and writable
        return {
            "status": "ok" if healthy else "degraded",
            "data_dir": current_repository.data_dir.as_posix(),
            "directory_generation": current_generation,
            "data_dir_exists": exists,
            "data_dir_readable": readable,
            "data_dir_writable": writable,
        }

    @app.get("/", include_in_schema=False, response_model=None)
    async def index() -> Response:
        index_path = STATIC_DIR / "index.html"
        if index_path.is_file():
            return FileResponse(index_path, media_type="text/html")
        return HTMLResponse(
            "<h1>标注前端尚未安装</h1><p>请确认 annotation_app/static/index.html 存在。</p>",
            status_code=503,
        )

    app.mount(
        "/static",
        StaticFiles(directory=str(STATIC_DIR), check_dir=False),
        name="static",
    )
    return app
