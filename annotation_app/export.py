"""将已保存标注及原图流式写入临时 ZIP，避免把原图载入内存。"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Font

from .repository import AnnotationRepository, InvalidAnnotationFileError, RepositoryError


EXCEL_HEADERS = (
    "照片文件名", "食物名称", "食物数量", "总重量", "设备型号",
    "容器类型", "附件类型", "层位", "食物尺寸",
)


class NoAnnotatedDataError(RepositoryError):
    def __init__(self) -> None:
        super().__init__("暂无已标注数据可下载")


@dataclass
class ExportArchive:
    path: Path
    filename: str
    temporary_directory: TemporaryDirectory

    def cleanup(self) -> None:
        self.temporary_directory.cleanup()


def _excel_value(value: Any, image_id: str) -> Any:
    if isinstance(value, list):
        value = "、".join(value)
    if isinstance(value, str) and (len(value) > 32767 or ILLEGAL_CHARACTERS_RE.search(value)):
        raise InvalidAnnotationFileError(f"标注含有 Excel 无法表示的文字：{image_id}")
    return value


def _write_workbook(rows: list[list[Any]], path: Path) -> None:
    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet("已标注数据")
    sheet.freeze_panes = "B2"
    widths = (42, 26, 14, 16, 18, 30, 30, 16, 16)
    for index, width in enumerate(widths):
        sheet.column_dimensions[chr(ord("A") + index)].width = width
    headers = [WriteOnlyCell(sheet, value=label) for label in EXCEL_HEADERS]
    for cell in headers:
        cell.font = Font(bold=True)
    sheet.append(headers)
    try:
        for row in rows:
            cells = []
            for value in row:
                cell = WriteOnlyCell(sheet, value=value)
                # 用户文字包括 =、+、-、@ 前缀均以字符串保存，不能成为 Excel 公式。
                if isinstance(value, str):
                    cell.data_type = "s"
                cells.append(cell)
            sheet.append(cells)
        sheet.auto_filter.ref = f"A1:I{len(rows) + 1}"
    finally:
        # write_only 工作表自身使用临时文件；save 会完成并释放这些文件。
        try:
            workbook.save(path)
        finally:
            workbook.close()


def create_export_archive(
    repository: AnnotationRepository,
    is_image_ready: Callable[[str], bool] | None = None,
) -> ExportArchive:
    temporary = TemporaryDirectory(prefix="annotation-export-")
    temporary_path = Path(temporary.name)
    archive_path = temporary_path / "download.zip"
    rows: list[list[Any]] = []
    try:
        with ZipFile(archive_path, "w", compression=ZIP_DEFLATED, compresslevel=1) as archive:
            for image_path in sorted(repository._iter_image_paths()):
                # 与保存、删除互斥，保证每一行对应打包的原图和该时刻的标注。
                # 单张图片打包后释放锁，避免整个下载生成过程阻塞标注操作。
                with repository.mutation_lock:
                    image_id = repository.image_id_for_path(image_path)
                    repository.resolve_image(image_id)
                    if is_image_ready is not None and not is_image_ready(image_id):
                        continue
                    sidecar = repository.checked_sidecar_path(image_path)
                    if not sidecar.is_file():
                        continue
                    document, _ = repository.read_annotation(image_id)
                    if document is None:
                        continue
                    values = document.annotations
                    row = [
                        image_id, values.food_name, values.food_count, values.quality,
                        values.device_model, values.container_type, values.accessory_type,
                        values.rack_level, values.food_size,
                    ]
                    rows.append([_excel_value(value, image_id) for value in row])
                    # 路径来自仓库的安全相对 ID；保留子目录以区分同名照片。
                    before = image_path.stat()
                    archive.write(image_path, arcname=image_id)
                    after = image_path.stat()
                    if (
                        (before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_ino)
                        != (after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_ino)
                        or (is_image_ready is not None and not is_image_ready(image_id))
                    ):
                        raise RepositoryError(f"导出时图像仍在写入或已经变化，请稍后重试：{image_id}")
            if not rows:
                raise NoAnnotatedDataError()
            workbook_path = temporary_path / "标注数据.xlsx"
            _write_workbook(rows, workbook_path)
            archive.write(workbook_path, arcname=workbook_path.name)
        return ExportArchive(
            archive_path,
            f"{repository.data_dir.name or '已标注数据'}.zip",
            temporary,
        )
    except BaseException:
        temporary.cleanup()
        raise
