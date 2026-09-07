"""FastAPI 应用工厂与本地标注 API。"""

from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import secrets
import shutil
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from tempfile import SpooledTemporaryFile
from typing import Any, AsyncIterator, BinaryIO, Literal, NoReturn
from urllib.parse import quote

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.types import Receive, Scope, Send

from .config import (
    ANNOTATION_VERSION,
    DEFAULT_DATA_DIR,
    FIELD_DEFINITIONS,
    STATIC_DIR,
)
from .models import SaveAnnotationRequest, SelectDataDirectoryRequest
from .export import ExportArchive, NoAnnotatedDataError, create_export_archive
from .repository import (
    AnnotationRepository,
    DeleteImageError,
    ImageNotFoundError,
    InvalidAnnotationFileError,
    InvalidImageIdError,
    RepositoryError,
    RevisionConflictError,
)
from .statistics import build_attribute_statistics
from .watcher import DirectoryChange, DirectorySyncService, FileStamp, ImageRecord


CLIENT_SESSION_COOKIE = "annotation_session_id"
CLIENT_SESSION_IDLE_SECONDS = 12 * 60 * 60
CLIENT_SESSION_REAP_INTERVAL_SECONDS = 5 * 60


@dataclass(slots=True)
class ClientDirectorySession:
    """Directory selection and watcher state owned by one browser session."""

    session_id: str
    repository: AnnotationRepository
    directory_sync: DirectorySyncService = field(default_factory=DirectorySyncService)
    directory_generation: int = 0
    directory_switch_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    start_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    started: bool = False
    active_streams: int = 0
    last_seen: float = field(default_factory=time.monotonic)

    def touch(self) -> None:
        self.last_seen = time.monotonic()

    async def ensure_started(self, *, wait_for_initial_scan: bool) -> None:
        if self.started:
            return
        async with self.start_lock:
            if self.started:
                return
            await self.directory_sync.start(
                self.repository.data_dir,
                self.directory_generation,
                wait_for_initial_scan=wait_for_initial_scan,
            )
            self.started = True

    async def stop(self) -> None:
        async with self.start_lock:
            if not self.started:
                return
            await self.directory_sync.stop()
            self.started = False


def _matches_stamp(metadata: os.stat_result, stamp: FileStamp, *, compare_ctime: bool = True) -> bool:
    return (
        metadata.st_size == stamp.size
        and metadata.st_mtime_ns == stamp.mtime_ns
        and (not compare_ctime or metadata.st_ctime_ns == stamp.ctime_ns)
        and metadata.st_ino == stamp.inode
    )


def _ready_image_paths(snapshot: dict[str, ImageRecord]) -> list[Path]:
    """只使用已完成校验且仍匹配磁盘版本的图片，避免重复遍历目录。"""
    paths = []
    for record in snapshot.values():
        try:
            metadata = record.path.stat()
        except OSError:
            continue
        if _matches_stamp(metadata, record.stamp):
            paths.append(record.path)
    return paths


def _image_not_ready() -> HTTPException:
    return HTTPException(
        status_code=425,
        detail={"code": "image_not_ready", "message": "照片仍在写入或尚未通过完整性检查，请稍后重试。"},
        headers={"Retry-After": "1", "Cache-Control": "no-store"},
    )


def _copy_image_snapshot(record: ImageRecord) -> BinaryIO:
    """固定一次已验证的图片内容，避免响应发送时原文件被覆盖造成破图。"""
    snapshot = SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
    try:
        if not _matches_stamp(record.path.stat(), record.stamp):
            raise _image_not_ready()
        with record.path.open("rb") as source:
            # Windows path.stat/fstat can report different ctime meanings;
            # retain full path checks and check descriptor identity/size/mtime.
            if not _matches_stamp(os.fstat(source.fileno()), record.stamp, compare_ctime=os.name != "nt"):
                raise _image_not_ready()
            shutil.copyfileobj(source, snapshot, length=256 * 1024)
            if not _matches_stamp(os.fstat(source.fileno()), record.stamp, compare_ctime=os.name != "nt"):
                raise _image_not_ready()
        if not _matches_stamp(record.path.stat(), record.stamp):
            raise _image_not_ready()
        snapshot.seek(0)
        return snapshot
    except BaseException:
        snapshot.close()
        raise


class ImageSnapshotResponse(StreamingResponse):
    def __init__(self, snapshot: BinaryIO, size: int, media_type: str) -> None:
        self.snapshot = snapshot
        super().__init__(
            iter(lambda: snapshot.read(256 * 1024), b""),
            media_type=media_type,
            headers={"Cache-Control": "no-cache", "Content-Length": str(size)},
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.snapshot.close()


class ExportFileResponse(FileResponse):
    """包括客户端断开/发送失败在内，都清理 ZIP 和 Excel 临时文件。"""

    def __init__(self, archive: ExportArchive) -> None:
        self.archive = archive
        super().__init__(
            archive.path,
            media_type="application/zip",
            filename=archive.filename,
            headers={"Cache-Control": "no-store"},
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.archive.cleanup()


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
    wait_for_initial_scan: bool = True,
) -> FastAPI:
    initial_repository = AnnotationRepository(data_dir or DEFAULT_DATA_DIR)
    if allowed_data_roots is None:
        resolved_allowed_roots = _configured_allowed_data_roots()
    else:
        resolved_allowed_roots = _resolve_allowed_data_roots(
            list(allowed_data_roots)
        )
    effective_allowed_roots = resolved_allowed_roots or (
        initial_repository.data_dir,
    )
    health_directory_sync = DirectorySyncService()
    client_sessions: dict[str, ClientDirectorySession] = {}
    retired_client_sessions: dict[str, ClientDirectorySession] = {}
    client_sessions_lock = asyncio.Lock()

    async def resolve_client_session(
        session_id: str | None,
        *,
        create: bool,
        start: bool,
    ) -> tuple[ClientDirectorySession | None, bool]:
        created = False
        async with client_sessions_lock:
            client_session = (
                client_sessions.get(session_id) if session_id else None
            )
            if client_session is None and create:
                new_session_id = secrets.token_urlsafe(32)
                while new_session_id in client_sessions:
                    new_session_id = secrets.token_urlsafe(32)
                client_session = ClientDirectorySession(
                    session_id=new_session_id,
                    repository=AnnotationRepository(
                        initial_repository.data_dir
                    ),
                )
                client_sessions[new_session_id] = client_session
                created = True
            if client_session is not None:
                client_session.touch()

        if client_session is not None and start:
            await client_session.ensure_started(
                wait_for_initial_scan=wait_for_initial_scan
            )
        return client_session, created

    async def reap_idle_client_sessions() -> None:
        while True:
            await asyncio.sleep(CLIENT_SESSION_REAP_INTERVAL_SECONDS)
            cutoff = time.monotonic() - CLIENT_SESSION_IDLE_SECONDS
            async with client_sessions_lock:
                expired = [
                    (session_id, client_session)
                    for session_id, client_session in client_sessions.items()
                    if (
                        client_session.active_streams == 0
                        and client_session.last_seen < cutoff
                    )
                ]
                for session_id, client_session in expired:
                    if client_sessions.get(session_id) is client_session:
                        client_sessions.pop(session_id, None)
                        retired_client_sessions[session_id] = client_session
            if not expired:
                continue
            await asyncio.gather(
                *(session.stop() for _, session in expired),
                return_exceptions=True,
            )
            async with client_sessions_lock:
                for session_id, client_session in expired:
                    if (
                        retired_client_sessions.get(session_id)
                        is client_session
                    ):
                        retired_client_sessions.pop(session_id, None)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        await health_directory_sync.start(
            initial_repository.data_dir,
            0,
            wait_for_initial_scan=wait_for_initial_scan,
        )
        session_reaper = asyncio.create_task(
            reap_idle_client_sessions(),
            name="annotation-client-session-reaper",
        )
        try:
            yield
        finally:
            session_reaper.cancel()
            await asyncio.gather(session_reaper, return_exceptions=True)
            async with client_sessions_lock:
                sessions = (
                    *client_sessions.values(),
                    *retired_client_sessions.values(),
                )
                client_sessions.clear()
                retired_client_sessions.clear()
            if sessions:
                await asyncio.gather(
                    *(session.stop() for session in sessions),
                    return_exceptions=True,
                )
            await health_directory_sync.stop()

    app = FastAPI(
        title="多属性图像标注工具",
        version="1.0.0",
        lifespan=lifespan,
    )
    # These initial-directory attributes remain useful to health checks and
    # operational tooling. Interactive requests use client_sessions instead.
    app.state.repository = initial_repository
    app.state.directory_generation = 0
    app.state.allowed_data_roots = effective_allowed_roots
    app.state.directory_sync = health_directory_sync
    app.state.client_sessions = client_sessions
    app.state.data_mutation_lock = asyncio.Lock()

    @app.middleware("http")
    async def bind_client_directory_session(
        request: Request,
        call_next: Any,
    ) -> Response:
        path = request.url.path
        is_health_check = path == "/api/v1/health"
        is_data_api = path.startswith("/api/v1/") and not is_health_check
        should_create = path == "/" or is_data_api
        client_session, created = await resolve_client_session(
            request.cookies.get(CLIENT_SESSION_COOKIE),
            create=should_create,
            start=is_data_api or is_health_check,
        )
        if client_session is not None:
            request.state.client_directory_session = client_session

        response = await call_next(request)
        if created and client_session is not None:
            response.set_cookie(
                CLIENT_SESSION_COOKIE,
                client_session.session_id,
                httponly=True,
                samesite="lax",
                secure=request.url.scheme == "https",
                path="/",
            )
        return response

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

    def client_session_for_request(request: Request) -> ClientDirectorySession:
        return request.state.client_directory_session

    def repository_for_generation(
        client_session: ClientDirectorySession,
        requested_generation: int | None,
    ) -> tuple[AnnotationRepository, int]:
        current_generation = client_session.directory_generation
        current_repository = client_session.repository
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
    async def get_config(http_request: Request) -> dict[str, Any]:
        client_session = client_session_for_request(http_request)
        current_repository = client_session.repository
        current_generation = client_session.directory_generation
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
        http_request: Request,
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

        client_session = client_session_for_request(http_request)
        current_repository, current_generation = repository_for_generation(
            client_session,
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
        http_request: Request,
        request: SelectDataDirectoryRequest | None = None,
        x_requested_with: str | None = Header(default=None),
    ) -> dict[str, Any]:
        client_session = client_session_for_request(http_request)
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
        if client_session.directory_switch_lock.locked():
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "directory_switch_busy",
                    "message": "另一个数据目录切换正在进行，请稍后重试。",
                },
            )
        async with client_session.directory_switch_lock:
            current_repository = client_session.repository
            current_generation = client_session.directory_generation
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
                new_generation = current_generation + 1
                await client_session.directory_sync.switch_directory(
                    candidate.data_dir,
                    new_generation,
                )
                client_session.repository = candidate
                client_session.directory_generation = new_generation
            return {
                "status": "selected",
                "changed": changed,
                "data_dir": candidate.data_dir.as_posix(),
                "directory_generation": client_session.directory_generation,
            }

    @app.get("/api/v1/images")
    async def get_images(
        http_request: Request,
        status: Literal["all", "labeled", "unlabeled", "invalid"] = "all",
        search: str = "",
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=100000, ge=1, le=100000),
        directory_generation: int | None = Query(default=None, ge=0),
    ) -> dict[str, Any]:
        client_session = client_session_for_request(http_request)
        current_repository, current_generation = repository_for_generation(
            client_session,
            directory_generation
        )
        directory_sync = client_session.directory_sync
        snapshot = directory_sync.ready_snapshot(current_generation)

        def collect_images() -> list[dict[str, Any]]:
            items = current_repository.list_images(_ready_image_paths(snapshot))
            # 校验期间发生的新覆写也不能从列表绕过预览就绪检查。
            return [item for item in items if directory_sync.is_ready(item["image_id"], current_generation)]

        try:
            all_items = await asyncio.to_thread(collect_images)
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

        client_session = client_session_for_request(request)
        _, current_generation = repository_for_generation(
            client_session,
            directory_generation,
        )
        directory_sync = client_session.directory_sync

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
            client_session.active_streams += 1
            client_session.touch()
            subscriber_id, event_queue = directory_sync.subscribe(
                current_generation
            )
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
                client_session.active_streams -= 1
                client_session.touch()

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
        http_request: Request,
        directory_generation: int | None = Query(default=None, ge=0),
    ) -> dict[str, Any]:
        client_session = client_session_for_request(http_request)
        current_repository, current_generation = repository_for_generation(
            client_session,
            directory_generation
        )
        directory_sync = client_session.directory_sync
        snapshot = directory_sync.ready_snapshot(current_generation)

        def collect_statistics() -> dict[str, Any]:
            summary, documents = current_repository.collect_annotation_documents(_ready_image_paths(snapshot))
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

    @app.get("/api/v1/export")
    async def export_annotations(
        http_request: Request,
        directory_generation: int = Query(ge=0),
    ) -> FileResponse:
        client_session = client_session_for_request(http_request)
        current_repository, current_generation = repository_for_generation(
            client_session,
            directory_generation,
        )
        directory_sync = client_session.directory_sync
        try:
            archive = await asyncio.to_thread(
                create_export_archive,
                current_repository,
                lambda image_id: directory_sync.is_ready(image_id, current_generation),
            )
        except NoAnnotatedDataError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "no_annotated_data", "message": str(exc)},
            ) from exc
        except RepositoryError as exc:
            _raise_repository_http(exc)
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail={"code": "export_failed", "message": f"导出失败：{exc}"},
            ) from exc
        return ExportFileResponse(archive)

    @app.delete("/api/v1/image")
    async def delete_image(
        http_request: Request,
        image_id: str = Query(min_length=1),
        directory_generation: int = Query(ge=0),
    ) -> dict[str, Any]:
        client_session = client_session_for_request(http_request)
        # 在锁内重新校验 generation，等待期间切换目录的请求不能继续删除。
        async with client_session.directory_switch_lock:
            current_repository, current_generation = repository_for_generation(
                client_session,
                directory_generation,
            )
            async with app.state.data_mutation_lock:
                try:
                    result = await asyncio.to_thread(
                        current_repository.delete_image,
                        image_id,
                    )
                except DeleteImageError as exc:
                    status_code = {
                        "shared_annotation": 409,
                        "image_not_found": 404,
                    }.get(exc.code, 500)
                    raise HTTPException(
                        status_code=status_code,
                        detail={
                            "code": exc.code,
                            "message": str(exc),
                            **exc.state,
                            "directory_generation": current_generation,
                        },
                    ) from exc
                except RepositoryError as exc:
                    _raise_repository_http(exc)
                except OSError as exc:
                    raise HTTPException(
                        status_code=500,
                        detail={
                            "code": "delete_failed",
                            "message": f"删除失败：{exc}",
                        },
                    ) from exc
            return {**result, "directory_generation": current_generation}

    @app.get("/api/v1/image")
    async def get_image(
        http_request: Request,
        image_id: str = Query(min_length=1),
        directory_generation: int | None = Query(default=None, ge=0),
    ) -> StreamingResponse:
        client_session = client_session_for_request(http_request)
        current_repository, current_generation = repository_for_generation(
            client_session,
            directory_generation,
        )
        directory_sync = client_session.directory_sync
        try:
            image_path = current_repository.resolve_image(image_id)
            canonical_id = current_repository.image_id_for_path(image_path)
        except RepositoryError as exc:
            _raise_repository_http(exc)

        record = directory_sync.ready_snapshot(current_generation).get(canonical_id)
        if record is None or record.path != image_path:
            raise _image_not_ready()
        try:
            snapshot = await asyncio.to_thread(_copy_image_snapshot, record)
        except FileNotFoundError as exc:
            raise _image_not_ready() from exc
        except OSError as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "image_unreadable", "message": f"暂时无法读取照片：{exc}"},
                headers={"Cache-Control": "no-store"},
            ) from exc
        try:
            repository_for_generation(client_session, current_generation)
        except HTTPException:
            snapshot.close()
            raise

        media_type, _ = mimetypes.guess_type(str(image_path))
        return ImageSnapshotResponse(snapshot, record.stamp.size, media_type or "application/octet-stream")

    @app.get("/api/v1/annotation")
    async def get_annotation(
        http_request: Request,
        image_id: str = Query(min_length=1),
        directory_generation: int | None = Query(default=None, ge=0),
    ) -> dict[str, Any]:
        client_session = client_session_for_request(http_request)
        current_repository, current_generation = repository_for_generation(
            client_session,
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
        http_request: Request,
        request: SaveAnnotationRequest,
        image_id: str = Query(min_length=1),
        directory_generation: int | None = Query(default=None, ge=0),
    ) -> dict[str, Any]:
        client_session = client_session_for_request(http_request)
        async with client_session.directory_switch_lock:
            current_repository, current_generation = repository_for_generation(
                client_session,
                directory_generation
            )
            try:
                image_path = current_repository.resolve_image(image_id)
                canonical_id = current_repository.image_id_for_path(image_path)
            except RepositoryError as exc:
                _raise_repository_http(exc)

            async with app.state.data_mutation_lock:
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
    async def get_health(
        http_request: Request,
        response: Response,
    ) -> dict[str, Any]:
        client_session = getattr(
            http_request.state,
            "client_directory_session",
            None,
        )
        if client_session is None:
            current_repository = initial_repository
            current_generation = 0
            directory_sync = health_directory_sync
        else:
            current_repository = client_session.repository
            current_generation = client_session.directory_generation
            directory_sync = client_session.directory_sync
        exists = current_repository.data_dir.is_dir()
        readable = os.access(current_repository.data_dir, os.R_OK)
        writable = os.access(current_repository.data_dir, os.W_OK)
        searchable = os.access(current_repository.data_dir, os.X_OK)
        healthy = exists and readable and writable and searchable
        response.status_code = 200 if healthy else 503
        return {
            "status": "ok" if healthy else "degraded",
            "data_dir": current_repository.data_dir.as_posix(),
            "directory_generation": current_generation,
            "initial_scan_complete": directory_sync.initial_scan_complete,
            "data_dir_exists": exists,
            "data_dir_readable": readable,
            "data_dir_writable": writable,
            "data_dir_searchable": searchable,
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
