#!/usr/bin/env python3
"""对 Linux 标注工具包及指定标注数据目录执行只读校验。"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from annotation_app.config import DEFAULT_DATA_DIR
from annotation_app.repository import (
    AnnotationRepository,
    InvalidAnnotationFileError,
    RepositoryError,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="校验标注图片与同名 JSON。")
    parser.add_argument(
        "data_dir",
        nargs="?",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="标注数据目录（默认：工具包内的 10-temp_label）",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    data_dir = args.data_dir.expanduser()
    if not data_dir.is_dir():
        print(f"错误：数据目录不存在或不是目录：{data_dir}", file=sys.stderr)
        return 2

    repository = AnnotationRepository.open_existing(data_dir)
    items = repository.list_images()
    counts = Counter(
        "无标注"
        if not item["annotation_exists"]
        else "有效标注"
        if item["annotation_valid"]
        else "无效标注"
        for item in items
    )

    print(f"数据目录：{repository.data_dir.as_posix()}")
    print(f"图片总数：{len(items)}")
    print(f"有效标注：{counts['有效标注']}")
    print(f"无标注：{counts['无标注']}")
    print(f"无效标注：{counts['无效标注']}")

    errors: list[str] = []
    for item in items:
        if not item["annotation_exists"]:
            continue
        try:
            repository.read_annotation(item["image_id"])
        except (InvalidAnnotationFileError, RepositoryError) as exc:
            errors.append(f"{item['image_id']}：{exc}")

    if errors:
        print("\n无法读取的标注：", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print("校验通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
