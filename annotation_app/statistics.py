"""已保存标注的属性值分布聚合。"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .models import AnnotationDocument


MISSING_VALUE_LABEL = "未填写"


def _display_value(value: Any) -> str:
    if value is None:
        return MISSING_VALUE_LABEL
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _value_sort_key(value: Any) -> tuple[int, Any]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return (0, float(value))
    return (1, _display_value(value).casefold())


def _unique_values(values: Iterable[Any]) -> list[Any]:
    result: list[Any] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def build_attribute_statistics(
    documents: Sequence[AnnotationDocument],
    field_definitions: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """按有效标注图片计数；多选字段的一张图片对每个选中值各计一次。"""
    valid_count = len(documents)
    annotation_values = [
        document.annotations.model_dump(mode="python") for document in documents
    ]
    fields: list[dict[str, Any]] = []

    for definition in field_definitions:
        field_name = str(definition["name"])
        multiple = bool(definition.get("multiple"))
        nullable = bool(definition.get("nullable"))
        counts: Counter[Any] = Counter()
        filled_count = 0

        for annotations in annotation_values:
            raw_value = annotations.get(field_name)
            values = raw_value if multiple and isinstance(raw_value, list) else [raw_value]
            values = _unique_values(values)
            if any(value is not None for value in values):
                filled_count += 1
            counts.update(values)

        configured_values = _unique_values(definition.get("options") or [])
        ordered_values = list(configured_values)
        observed_extras = [
            value
            for value, count in counts.items()
            if count > 0 and value is not None and value not in configured_values
        ]
        observed_extras.sort(key=lambda value: (-counts[value], _value_sort_key(value)))
        ordered_values.extend(observed_extras)

        if not configured_values:
            ordered_values.sort(key=lambda value: (-counts[value], _value_sort_key(value)))
        if nullable:
            ordered_values.append(None)

        distribution = []
        for value in _unique_values(ordered_values):
            count = counts[value]
            distribution.append(
                {
                    "value": value,
                    "label": _display_value(value),
                    "count": count,
                    "percentage": round(count * 100 / valid_count, 1)
                    if valid_count
                    else 0.0,
                    "is_missing": value is None,
                }
            )

        fields.append(
            {
                "name": field_name,
                "label": str(definition.get("label") or field_name),
                "type": str(definition.get("type") or "text"),
                "multiple": multiple,
                "nullable": nullable,
                "valid_annotations": valid_count,
                "filled": filled_count,
                "missing": valid_count - filled_count,
                "observed_values": sum(
                    count > 0 and value is not None
                    for value, count in counts.items()
                ),
                "values": distribution,
            }
        )

    return fields
