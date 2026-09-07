"""应用常量与前端字段配置。"""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = "/data/wangzhuo/66-newdata/00-dataset"
STATIC_DIR = Path(__file__).resolve().parent / "static"
ANNOTATION_VERSION = "1.6"
MISSING_REVISION = "__missing__"

IMAGE_SUFFIXES = {
    ".bmp",
    ".gif",
    ".jpeg",
    ".jpg",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
}

# 当前容器类别；历史标签名称由解析器兼容转换。
CONTAINER_TYPES = (
    "无",
    "玻璃容器",
    "塑料容器",
    "陶瓷容器",
    "泡沫容器",
    "金属容器",
    "木质/竹制容器",
    "纸质容器",
    "油纸",
    "珐琅锅",
    "保鲜膜",
    "铝箔纸",
)

ACCESSORY_TYPES = (
    "无",
    "玻璃蒸烤盘",
    "脆烤盘",
    "微波专用烤架",
    "烤架",
    "炸烤网架",
    "烤盘",
    "有孔蒸盘",
    "无孔蒸盘",
    "小炸篮",
    "转轴烤叉",
    "炸烤盘",
)

RACK_LEVELS = ("无", "0", "1", "2", "3", "4", "5")
DEVICE_MODELS = ("C9277A", "CQ09-i9", "C87-i7Pro", "DB677")

FIELD_DEFINITIONS = (
    {
        "name": "food_name",
        "label": "食物名称",
        "type": "text",
        "json_type": "string",
        "multiple": False,
        "default": "无",
    },
    {
        "name": "food_count",
        "label": "食物数量",
        "type": "integer",
        "json_type": "int | null",
        "multiple": False,
        "nullable": True,
        "default": None,
    },
    {
        "name": "quality",
        "label": "总重量（g）",
        "type": "number",
        "json_type": "float | null",
        "multiple": False,
        "nullable": True,
        "default": None,
    },
    {
        "name": "device_model",
        "label": "设备型号",
        "type": "select",
        "json_type": "string | null",
        "multiple": False,
        "nullable": True,
        "default": None,
        "options": list(DEVICE_MODELS),
    },
    {
        "name": "container_type",
        "label": "容器类型",
        "type": "select",
        "json_type": "list[string]",
        "multiple": True,
        "default": ["无"],
        "options": list(CONTAINER_TYPES),
    },
    {
        "name": "accessory_type",
        "label": "附件类型",
        "type": "select",
        "json_type": "list[string]",
        "multiple": True,
        "default": ["无"],
        "options": list(ACCESSORY_TYPES),
    },
    {
        "name": "rack_level",
        "label": "层位",
        "type": "select",
        "json_type": "list[string]",
        "multiple": True,
        "default": ["无"],
        "options": list(RACK_LEVELS),
    },
    {
        "name": "food_size",
        "label": "食物尺寸",
        "type": "integer",
        "json_type": "int | null",
        "multiple": False,
        "nullable": True,
        "default": None,
    },
)
