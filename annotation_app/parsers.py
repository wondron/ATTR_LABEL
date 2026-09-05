"""模型输出及标注字段的规范化逻辑。"""

from __future__ import annotations

import csv
import io
import math
import re
from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from .config import ACCESSORY_TYPES, CONTAINER_TYPES, DEVICE_MODELS, RACK_LEVELS


TSV_FIELDS = (
    "food_name",
    "food_count",
    "container_type",
    "accessory_type",
    "quality",
    "rack_level",
    "food_size",
)


class TSVParseError(ValueError):
    """多属性模型输出不符合约定的 TSV 格式。"""


_LEADING_THINK_BLOCK = re.compile(
    r"\A\s*<think(?:\s[^>]*)?>.*?</think>\s*",
    flags=re.IGNORECASE | re.DOTALL,
)

# 兼容历史数据和旧模型输出；右侧值才是当前唯一合法枚举。
_ENUM_VALUE_ALIASES = {
    "container_type": {
        "铝箔": "铝箔、锡箔纸",
        "锡箔纸": "铝箔、锡箔纸",
    },
}


def _remove_optional_think_block(text: str) -> str:
    """移除推理模型可能附加在最终答案前的 think 块。"""
    match = _LEADING_THINK_BLOCK.match(text)
    if match is not None:
        return text[match.end() :]
    if re.match(r"\A\s*<think\b", text, flags=re.IGNORECASE):
        raise TSVParseError("模型输出包含未闭合的 <think> 推理块。")
    return text


def _remove_optional_code_fence(text: str) -> str:
    lines = text.splitlines()
    if not lines:
        return text

    first = lines[0].strip().lower()
    if first.startswith("```"):
        if len(lines) < 2 or lines[-1].strip() != "```":
            raise TSVParseError("模型输出包含未闭合的 Markdown 代码块。")
        return "\n".join(lines[1:-1])
    return text


def _segment_enum_values(value: str, allowed: Iterable[str], field_name: str) -> list[str]:
    value = value.strip()
    allowed_values = tuple(dict.fromkeys(item.strip() for item in allowed))
    allowed_set = set(allowed_values)

    if not value or value == "无":
        return ["无"]
    if value in allowed_set:
        return [value]

    # 用允许值反向拼接，而不是直接 split("、")；部分合法枚举本身带顿号。
    candidates = sorted(
        (item for item in allowed_values if item != "无"),
        key=len,
        reverse=True,
    )

    def walk(remaining: str) -> list[str] | None:
        for candidate in candidates:
            if remaining == candidate:
                return [candidate]
            prefix = candidate + "、"
            if remaining.startswith(prefix):
                tail = walk(remaining[len(prefix) :])
                if tail is not None:
                    return [candidate, *tail]
        return None

    result = walk(value)
    if result is None:
        raise ValueError(f"{field_name} 包含不支持的值：{value!r}")
    return list(dict.fromkeys(result))


def normalize_food_count(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("food_count 必须是整数或空值。")
    if isinstance(value, int):
        result = value
    elif isinstance(value, float) and value.is_integer():
        result = int(value)
    elif isinstance(value, str):
        stripped = value.strip()
        if stripped in {"", "无", "未知", "null", "None"}:
            return None
        if not re.fullmatch(r"[+-]?\d+", stripped):
            raise ValueError(f"food_count 不是有效整数：{value!r}")
        result = int(stripped)
    else:
        raise ValueError("food_count 必须是整数或空值。")

    if result < 0:
        raise ValueError("food_count 不能小于 0。")
    return result


def _normalize_number(value: Any, field_name: str) -> int | float | None:
    """把 JSON 数字或旧版数字字符串规范成真正的 JSON 数字。"""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field_name} 必须是数字或空值。")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{field_name} 必须是有限数字。")
        return int(value) if value.is_integer() else value
    if not isinstance(value, str):
        raise ValueError(f"{field_name} 必须是数字或空值。")

    stripped = value.strip()
    if stripped.casefold() in {"", "无", "未知", "null", "none"}:
        return None
    try:
        decimal_value = Decimal(stripped)
    except InvalidOperation as exc:
        raise ValueError(f"{field_name} 不是有效数字：{value!r}") from exc
    if not decimal_value.is_finite():
        raise ValueError(f"{field_name} 必须是有限数字。")
    if decimal_value == decimal_value.to_integral_value():
        return int(decimal_value)
    result = float(decimal_value)
    if not math.isfinite(result):
        raise ValueError(f"{field_name} 超出可保存的数字范围。")
    return result


def normalize_quality(value: Any) -> float | None:
    """规范为 JSON 浮点数；旧版整数和数字字符串仍可读取。"""
    number = _normalize_number(value, "quality")
    if number is None:
        return None
    result = float(number)
    if not math.isfinite(result):
        raise ValueError("quality 超出可保存的数字范围。")
    return result


def normalize_device_model(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("device_model 必须是字符串或空值。")
    normalized = value.strip()
    if normalized in {"", "None", "none", "null", "无"}:
        return None
    if normalized not in DEVICE_MODELS:
        raise ValueError(f"device_model 包含不支持的值：{normalized!r}")
    return normalized


def _as_text_items(value: Any, field_name: str) -> list[str]:
    if value is None:
        return ["无"]
    if isinstance(value, str):
        return [value]
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} 必须是字符串数组。")

    result: list[str] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (str, int, float)):
            raise ValueError(f"{field_name} 必须是字符串数组。")
        result.append(str(item))
    return result or ["无"]


def normalize_enum_list(value: Any, *, allowed: Iterable[str], field_name: str) -> list[str]:
    normalized: list[str] = []
    aliases = _ENUM_VALUE_ALIASES.get(field_name, {})
    segment_allowed = (*allowed, *aliases.keys())
    for raw_item in _as_text_items(value, field_name):
        item = raw_item.strip()
        if field_name == "rack_level":
            item = item.replace("底板层", "0")
        normalized.extend(
            aliases.get(segment, segment)
            for segment in _segment_enum_values(item, segment_allowed, field_name)
        )

    normalized = list(dict.fromkeys(normalized))
    if "无" in normalized and len(normalized) > 1:
        raise ValueError(f"{field_name} 的“无”不能与其他值同时选择。")
    return normalized or ["无"]


def normalize_food_size(value: Any) -> int | None:
    """规范为单个正整数；兼容旧版空数组和单元素数组。"""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("food_size 必须是整数或空值。")

    if isinstance(value, (list, tuple)):
        if not value:
            return None
        if len(value) != 1:
            raise ValueError("food_size 必须是单个整数，不能包含多个尺寸。")
        value = value[0]

    if isinstance(value, str) and "、" in value:
        parts = [part.strip() for part in value.split("、") if part.strip()]
        if len(parts) != 1:
            raise ValueError("food_size 必须是单个整数，不能包含多个尺寸。")
        value = parts[0] if parts else ""

    number = _normalize_number(value, "food_size")
    if number is None:
        return None
    if isinstance(number, float) and not number.is_integer():
        raise ValueError(f"food_size 必须是整数：{value!r}")
    result = int(number)
    if result <= 0:
        raise ValueError(f"food_size 必须大于 0：{value!r}")
    return result


def normalize_annotations(raw: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("annotations 必须是 JSON 对象。")

    rack_value = raw.get("rack_level")

    food_name = raw.get("food_name", "无")
    if food_name is None:
        food_name = "无"
    if not isinstance(food_name, str):
        raise ValueError("food_name 必须是字符串。")
    food_name = food_name.strip() or "无"

    return {
        "food_name": food_name,
        "food_count": normalize_food_count(raw.get("food_count")),
        "quality": normalize_quality(raw.get("quality")),
        "device_model": normalize_device_model(raw.get("device_model")),
        "container_type": normalize_enum_list(
            raw.get("container_type"),
            allowed=CONTAINER_TYPES,
            field_name="container_type",
        ),
        "accessory_type": normalize_enum_list(
            raw.get("accessory_type"),
            allowed=ACCESSORY_TYPES,
            field_name="accessory_type",
        ),
        "rack_level": normalize_enum_list(
            rack_value,
            allowed=RACK_LEVELS,
            field_name="rack_level",
        ),
        "food_size": normalize_food_size(raw.get("food_size")),
    }


def parse_label_tsv(text: str) -> dict[str, Any]:
    if not isinstance(text, str) or not text.strip():
        raise TSVParseError("模型输出为空。")

    cleaned = text.lstrip("\ufeff")
    cleaned = _remove_optional_think_block(cleaned)
    cleaned = _remove_optional_code_fence(cleaned)
    cleaned = _remove_optional_think_block(cleaned)
    lines = [line for line in cleaned.splitlines() if line.strip()]
    if len(lines) != 2:
        raise TSVParseError(f"模型输出必须恰好包含两行 TSV，实际为 {len(lines)} 行。")

    try:
        rows = list(csv.reader(io.StringIO("\n".join(lines)), delimiter="\t", strict=True))
    except csv.Error as exc:
        raise TSVParseError(f"TSV 解析失败：{exc}") from exc

    if len(rows) != 2:
        raise TSVParseError("模型输出必须包含表头和一条数据。")
    header = tuple(cell.strip() for cell in rows[0])
    if header != TSV_FIELDS:
        raise TSVParseError(
            "TSV 表头不正确；期望：" + "\t".join(TSV_FIELDS)
        )
    if len(rows[1]) != len(TSV_FIELDS):
        raise TSVParseError(
            f"TSV 数据行必须包含 7 列，实际为 {len(rows[1])} 列。"
        )

    values = dict(zip(TSV_FIELDS, (cell.strip() for cell in rows[1]), strict=True))
    try:
        return normalize_annotations(values)
    except ValueError as exc:
        raise TSVParseError(str(exc)) from exc


def normalize_food_result(result: Any) -> str:
    """从食物检测服务的常见返回结构提取食物名称。"""
    if isinstance(result, str):
        name = result.strip()
        if name:
            return name
        raise ValueError("食物检测结果为空字符串。")

    if isinstance(result, Mapping):
        for key in ("food_name", "name", "label", "result"):
            if key in result:
                try:
                    return normalize_food_result(result[key])
                except ValueError:
                    continue
        raise ValueError("食物检测字典中没有可识别的名称字段。")

    if isinstance(result, (list, tuple)):
        for item in result:
            try:
                return normalize_food_result(item)
            except ValueError:
                continue
        raise ValueError("食物检测列表中没有可识别的名称。")

    raise ValueError(f"无法识别食物检测结果类型：{type(result).__name__}")
