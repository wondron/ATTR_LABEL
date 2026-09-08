"""图像目录访问、sidecar JSON 兼容读取与原子保存。"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
from collections import OrderedDict
from collections.abc import Iterable, Iterator
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from threading import RLock
from typing import Any

from pydantic import ValidationError

from .config import ANNOTATION_VERSION, IMAGE_SUFFIXES, MISSING_REVISION
from .models import AnnotationDocument, AnnotationValues


class RepositoryError(RuntimeError):
    """标注仓库错误基类。"""


class InvalidImageIdError(RepositoryError):
    """客户端提供了不安全或不支持的图像 ID。"""


class ImageNotFoundError(RepositoryError):
    """图像不存在。"""


class InvalidAnnotationFileError(RepositoryError):
    """已有 sidecar 文件无法解析。"""


class DeleteImageError(RepositoryError):
    """删除失败，携带已经核实的磁盘状态。"""

    def __init__(self, message: str, state: dict[str, Any], *, code: str = "delete_failed") -> None:
        super().__init__(message)
        self.state = state
        self.code = code


class RevisionConflictError(RepositoryError):
    """保存时已有标注被其他操作更新。"""

    def __init__(self, current_revision: str | None) -> None:
        super().__init__("标注已被其他操作更新，请刷新后重试。")
        self.current_revision = current_revision


def utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def file_revision(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _create_atomic_temporary_file(
    sidecar: Path,
    preserved_mode: int | None,
) -> tuple[int, Path]:
    """Create a same-directory temporary file with the final sidecar mode."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_BINARY", 0)

    for _ in range(100):
        temporary_path = sidecar.parent / (
            f".{sidecar.stem}.{secrets.token_hex(8)}.tmp"
        )
        try:
            descriptor = os.open(temporary_path, flags, 0o666)
        except FileExistsError:
            continue

        try:
            if preserved_mode is not None:
                os.chmod(temporary_path, preserved_mode)
        except BaseException:
            os.close(descriptor)
            try:
                temporary_path.unlink()
            except OSError:
                pass
            raise
        return descriptor, temporary_path

    raise FileExistsError(f"无法为标注创建唯一临时文件：{sidecar.name}")


class AnnotationRepository:
    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = self._prepare_data_dir(data_dir, create=True)
        self._initialize_state()

    def _initialize_state(self) -> None:
        # 所有写入和删除共用锁；同 stem 不同扩展也可能共享一个 JSON。
        self.mutation_lock = RLock()
        self._document_cache: OrderedDict[Path, tuple[tuple[int, ...], Any]] = OrderedDict()

    @staticmethod
    def _prepare_data_dir(data_dir: str | Path, *, create: bool) -> Path:
        requested_dir = Path(data_dir).expanduser()
        if create:
            requested_dir.mkdir(parents=True, exist_ok=True)
        elif not requested_dir.exists():
            raise FileNotFoundError(f"所选文件夹不存在：{requested_dir}")
        if not requested_dir.is_dir():
            raise NotADirectoryError(f"标注数据路径不是文件夹：{requested_dir}")
        return requested_dir.resolve()

    @staticmethod
    def _ensure_data_dir_access(data_dir: Path) -> None:
        if not os.access(data_dir, os.R_OK):
            raise PermissionError(f"所选文件夹不可读取：{data_dir}")
        if not os.access(data_dir, os.W_OK):
            raise PermissionError(f"所选文件夹不可写入：{data_dir}")
        if not os.access(data_dir, os.X_OK):
            raise PermissionError(f"所选文件夹不可进入：{data_dir}")

    @classmethod
    def open_existing(cls, data_dir: str | Path) -> AnnotationRepository:
        """打开已存在且可读写的标注目录，不隐式创建路径。"""
        repository = cls.__new__(cls)
        resolved = cls._prepare_data_dir(data_dir, create=False)
        cls._ensure_data_dir_access(resolved)
        repository.data_dir = resolved
        repository._initialize_state()
        return repository

    def set_data_dir(self, data_dir: str | Path) -> Path:
        """切换后续读取和保存使用的数据目录。"""
        resolved = self._prepare_data_dir(data_dir, create=False)
        self._ensure_data_dir_access(resolved)
        self.data_dir = resolved
        self._initialize_state()
        return resolved

    def _normalize_id(self, image_id: str) -> PurePosixPath:
        if not isinstance(image_id, str):
            raise InvalidImageIdError("image_id 必须是字符串。")
        image_id = image_id.strip().replace("\\", "/")
        if not image_id or any(ord(character) < 32 for character in image_id):
            raise InvalidImageIdError("image_id 不能为空。")

        relative = PurePosixPath(image_id)
        if relative.is_absolute() or any(
            part in {"", ".", ".."} or ":" in part for part in relative.parts
        ):
            raise InvalidImageIdError("image_id 必须是数据目录内的安全相对路径。")
        return relative

    def _safe_image_path(self, image_id: str) -> Path:
        relative = self._normalize_id(image_id)
        candidate = self.data_dir
        for part in relative.parts:
            candidate = candidate / part
            if candidate.is_symlink() or (
                hasattr(candidate, "is_junction") and candidate.is_junction()
            ):
                raise InvalidImageIdError("不支持通过符号链接访问或删除图像。")
        resolved = candidate.resolve(strict=False)
        if not resolved.is_relative_to(self.data_dir):
            raise InvalidImageIdError("image_id 指向了数据目录之外。")
        if resolved.suffix.lower() not in IMAGE_SUFFIXES:
            raise InvalidImageIdError(f"不支持的图像类型：{resolved.suffix or '(无后缀)'}")
        return resolved

    def resolve_image(self, image_id: str) -> Path:
        candidate = self._safe_image_path(image_id)
        if not candidate.is_file():
            raise ImageNotFoundError(f"图像不存在：{image_id}")
        return candidate

    def image_id_for_path(self, image_path: Path) -> str:
        resolved = image_path.resolve()
        try:
            relative = resolved.relative_to(self.data_dir)
        except ValueError as exc:
            raise InvalidImageIdError("图像不在数据目录内。") from exc
        return relative.as_posix()

    @staticmethod
    def sidecar_path(image_path: Path) -> Path:
        return image_path.with_suffix(".json")

    def checked_sidecar_path(self, image_path: Path) -> Path:
        sidecar = self.sidecar_path(image_path)
        if sidecar.is_symlink() or not sidecar.resolve(strict=False).is_relative_to(self.data_dir):
            raise InvalidAnnotationFileError(f"标注文件不能是符号链接或指向目录之外：{sidecar.name}")
        if sidecar.exists() and not sidecar.is_file():
            raise InvalidAnnotationFileError(f"标注路径不是普通文件：{sidecar.name}")
        return sidecar

    def _iter_image_paths(self) -> Iterator[Path]:
        for path in self.data_dir.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            try:
                resolved = self._safe_image_path(path.relative_to(self.data_dir).as_posix())
            except (OSError, ValueError, RepositoryError):
                continue
            yield resolved

    def list_images(self, image_paths: Iterable[Path] | None = None) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for resolved in self._iter_image_paths() if image_paths is None else image_paths:
            image_id = self.image_id_for_path(resolved)
            sidecar = self.sidecar_path(resolved)
            annotation_exists = sidecar.is_file()
            annotation_valid: bool | None = None
            annotation_error: str | None = None
            revision: str | None = None
            annotations: dict[str, Any] | None = None
            if annotation_exists:
                try:
                    document, revision = self._read_document(resolved)
                    annotations = document.annotations.model_dump(mode="json")
                    annotation_valid = True
                except InvalidAnnotationFileError as exc:
                    annotation_valid = False
                    annotation_error = str(exc)
                    try:
                        revision = file_revision(sidecar)
                    except OSError:
                        revision = None
            else:
                # 未标注图像按表单默认值参与筛选；损坏的标注保留为 None。
                annotations = AnnotationValues().model_dump(mode="json")

            try:
                stat = resolved.stat()
            except FileNotFoundError:
                # 扫描过程中允许外部相机/文件管理器移走文件。
                continue
            items.append(
                {
                    "image_id": image_id,
                    "name": resolved.name,
                    "relative_dir": PurePosixPath(image_id).parent.as_posix()
                    if "/" in image_id
                    else "",
                    "annotation_exists": annotation_exists,
                    "annotation_valid": annotation_valid,
                    "annotation_error": annotation_error,
                    "annotations": annotations,
                    "revision": revision,
                    "size": stat.st_size,
                    "modified_at": datetime.fromtimestamp(
                        stat.st_mtime, tz=timezone.utc
                    ).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                }
            )

        return sorted(items, key=lambda item: item["image_id"].casefold())

    def collect_annotation_documents(
        self,
        image_paths: Iterable[Path] | None = None,
    ) -> tuple[dict[str, int], list[AnnotationDocument]]:
        """单次扫描目录并返回统计基数与所有有效标注文档。"""
        total = 0
        labeled = 0
        invalid = 0
        documents: list[AnnotationDocument] = []

        for image_path in self._iter_image_paths() if image_paths is None else image_paths:
            total += 1
            sidecar = self.sidecar_path(image_path)
            if not sidecar.is_file():
                continue
            labeled += 1
            try:
                document, _ = self._read_document(image_path)
            except InvalidAnnotationFileError:
                invalid += 1
                continue
            documents.append(document)

        return (
            {
                "total": total,
                "labeled": labeled,
                "valid": len(documents),
                "unlabeled": total - labeled,
                "invalid": invalid,
            },
            documents,
        )

    def annotation_exists(self, image_id: str) -> bool:
        image_path = self.resolve_image(image_id)
        return self.sidecar_path(image_path).is_file()

    def read_annotation(self, image_id: str) -> tuple[AnnotationDocument | None, str | None]:
        image_path = self.resolve_image(image_id)
        sidecar = self.sidecar_path(image_path)
        if not sidecar.is_file():
            return None, MISSING_REVISION
        return self._read_document(image_path)

    def current_revision(self, image_id: str) -> str:
        """返回 sidecar 当前版本；不存在时返回专用哨兵值。"""
        image_path = self.resolve_image(image_id)
        sidecar = self.sidecar_path(image_path)
        if not sidecar.is_file():
            return MISSING_REVISION
        try:
            return file_revision(sidecar)
        except OSError as exc:
            raise RepositoryError(f"无法读取已有标注：{sidecar.name}") from exc

    def _read_document(self, image_path: Path) -> tuple[AnnotationDocument, str]:
        with self.mutation_lock:
            sidecar = self.checked_sidecar_path(image_path)
            try:
                metadata = sidecar.stat()
            except OSError as exc:
                raise InvalidAnnotationFileError(f"无法读取标注文件：{sidecar.name}：{exc}") from exc
            signature = (metadata.st_mtime_ns, metadata.st_ctime_ns, metadata.st_size, metadata.st_ino)
            cached = self._document_cache.get(image_path)
            if cached is not None and cached[0] == signature:
                self._document_cache.move_to_end(image_path)
                if isinstance(cached[1], str):
                    raise InvalidAnnotationFileError(cached[1])
                return cached[1]
            try:
                result = self._read_uncached_document(image_path)
            except InvalidAnnotationFileError as exc:
                self._document_cache[image_path] = (signature, str(exc))
                raise
            else:
                self._document_cache[image_path] = (signature, result)
                return result
            finally:
                while len(self._document_cache) > 32768:
                    self._document_cache.popitem(last=False)

    def _read_uncached_document(self, image_path: Path) -> tuple[AnnotationDocument, str]:
        sidecar = self.checked_sidecar_path(image_path)
        try:
            raw_bytes = sidecar.read_bytes()
            raw = json.loads(raw_bytes.decode("utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise InvalidAnnotationFileError(
                f"标注文件不是有效的 UTF-8 JSON：{sidecar.name}"
            ) from exc
        if not isinstance(raw, dict):
            raise InvalidAnnotationFileError(f"标注文件根节点必须是对象：{sidecar.name}")

        raw_annotations = raw.get("annotations")
        if not isinstance(raw_annotations, dict):
            raise InvalidAnnotationFileError(
                f"标注文件缺少 annotations 对象：{sidecar.name}"
            )

        updated_at = raw.get("updated_at")
        if not isinstance(updated_at, str) or not updated_at.strip():
            updated_at = datetime.fromtimestamp(
                sidecar.stat().st_mtime, tz=timezone.utc
            ).isoformat(timespec="milliseconds").replace("+00:00", "Z")

        try:
            document = AnnotationDocument(
                image=image_path.name,
                image_name=image_path.name,
                image_root=str(raw.get("image_root") or image_path.parent.as_posix()),
                annotation_version=str(
                    raw.get("annotation_version") or ANNOTATION_VERSION
                ),
                updated_at=updated_at,
                annotations=AnnotationValues.model_validate(raw_annotations),
            )
        except (ValidationError, ValueError, TypeError) as exc:
            raise InvalidAnnotationFileError(
                f"标注文件字段无效：{sidecar.name}：{exc}"
            ) from exc

        return document, hashlib.sha256(raw_bytes).hexdigest()

    def save_annotation(
        self,
        image_id: str,
        annotations: AnnotationValues,
        expected_revision: str = MISSING_REVISION,
    ) -> tuple[AnnotationDocument, str]:
        with self.mutation_lock:
            return self._save_annotation(image_id, annotations, expected_revision)

    def _save_annotation(
        self,
        image_id: str,
        annotations: AnnotationValues,
        expected_revision: str,
    ) -> tuple[AnnotationDocument, str]:
        image_path = self.resolve_image(image_id)
        sidecar = self.checked_sidecar_path(image_path)

        current_revision: str | None = None
        preserved_mode: int | None = None
        if sidecar.is_file():
            try:
                current_revision = file_revision(sidecar)
                preserved_mode = stat.S_IMODE(sidecar.stat().st_mode)
            except OSError as exc:
                raise RepositoryError(f"无法读取已有标注：{sidecar.name}") from exc

        comparable_revision = (
            MISSING_REVISION if current_revision is None else current_revision
        )
        if expected_revision != comparable_revision:
            raise RevisionConflictError(current_revision)

        document = AnnotationDocument(
            image=image_path.name,
            image_name=image_path.name,
            image_root=image_path.parent.as_posix(),
            annotation_version=ANNOTATION_VERSION,
            updated_at=utc_now_iso(),
            annotations=annotations,
        )
        serialized = (
            json.dumps(
                document.model_dump(mode="json"),
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
        encoded = serialized.encode("utf-8")

        temporary_path: Path | None = None
        try:
            descriptor, temporary_path = _create_atomic_temporary_file(
                sidecar,
                preserved_mode,
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, sidecar)
        except OSError as exc:
            raise RepositoryError(f"保存标注失败：{sidecar.name}：{exc}") from exc
        finally:
            if temporary_path is not None and temporary_path.exists():
                try:
                    temporary_path.unlink()
                except OSError:
                    pass

        return document, hashlib.sha256(encoded).hexdigest()

    def delete_image(self, image_id: str) -> dict[str, Any]:
        """先删除原图再删除 JSON；失败时保留其余文件并报告实际状态。"""
        with self.mutation_lock:
            image_path = self._safe_image_path(image_id)
            sidecar = self.sidecar_path(image_path)

            def state() -> dict[str, Any]:
                def exists(path: Path) -> bool | None:
                    try:
                        path.lstat()
                        return True
                    except FileNotFoundError:
                        return False
                    except OSError:
                        return None
                return {
                    "image_id": image_id,
                    "image_exists": exists(image_path),
                    "annotation_exists": exists(sidecar),
                }

            try:
                if not image_path.is_file():
                    raise DeleteImageError(f"图像不存在：{image_id}", state(), code="image_not_found")
                self.checked_sidecar_path(image_path)
                if sidecar.exists():
                    siblings = [
                        path.name for path in image_path.parent.iterdir()
                        if path != image_path and path.suffix.lower() in IMAGE_SUFFIXES
                        and path.with_suffix(".json") == sidecar and path.is_file()
                    ]
                    if siblings:
                        raise DeleteImageError(
                            "标注文件与其他图像共用，无法安全删除：" + "、".join(siblings),
                            state(), code="shared_annotation",
                        )
                image_path.unlink()
                sidecar.unlink(missing_ok=True)
            except DeleteImageError:
                raise
            except (OSError, RepositoryError) as exc:
                raise DeleteImageError(f"删除失败：{exc}", state()) from exc
            finally:
                self._document_cache.pop(image_path, None)
            return {"status": "deleted", **state()}
