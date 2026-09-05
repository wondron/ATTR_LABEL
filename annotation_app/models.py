"""FastAPI 请求模型与标注数据模型。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .config import ANNOTATION_VERSION
from .parsers import normalize_annotations


class AnnotationValues(BaseModel):
    model_config = ConfigDict(extra="forbid")

    food_name: str = "无"
    food_count: int | None = Field(default=None, ge=0)
    quality: float | None = None
    device_model: str | None = None
    container_type: list[str] = Field(default_factory=lambda: ["无"])
    accessory_type: list[str] = Field(default_factory=lambda: ["无"])
    rack_level: list[str] = Field(default_factory=lambda: ["无"])
    food_size: int | None = Field(default=None, gt=0)

    @model_validator(mode="before")
    @classmethod
    def normalize_input(cls, value: Any) -> Any:
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict):
            raise ValueError("annotations 必须是 JSON 对象。")
        return normalize_annotations(value)


class AnnotationDocument(BaseModel):
    model_config = ConfigDict(extra="ignore")

    image: str
    image_name: str
    image_root: str
    annotation_version: str = ANNOTATION_VERSION
    updated_at: str
    annotations: AnnotationValues


class SelectDataDirectoryRequest(BaseModel):
    """浏览器提交的服务器端标注数据目录。"""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=4096)
    directory_generation: int = Field(ge=0)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("path 不能为空。")
        if any(ord(character) < 32 for character in value):
            raise ValueError("path 包含非法控制字符。")
        return value


class SaveAnnotationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    annotations: AnnotationValues
    revision: str

    @model_validator(mode="before")
    @classmethod
    def validate_annotation_json_types(cls, value: Any) -> Any:
        """保存接口只接受目标 JSON 类型；历史兼容转换仅用于读取旧标注。"""
        if not isinstance(value, dict):
            return value

        annotations = value.get("annotations")
        if annotations is None:
            return value
        if not isinstance(annotations, dict):
            raise ValueError("annotations 必须是 JSON 对象。")

        allowed_fields = set(AnnotationValues.model_fields)
        unexpected = sorted(set(annotations) - allowed_fields)
        if unexpected:
            raise ValueError(
                "annotations 包含未知字段：" + "、".join(unexpected)
            )

        def require_exact_type(
            field_name: str,
            expected_type: type,
            display_name: str,
            *,
            nullable: bool = False,
        ) -> None:
            if field_name not in annotations:
                return
            field_value = annotations[field_name]
            if nullable and field_value is None:
                return
            if type(field_value) is not expected_type:
                suffix = " 或 null" if nullable else ""
                raise ValueError(f"{field_name} 必须是 {display_name}{suffix}。")

        require_exact_type("food_name", str, "字符串")
        require_exact_type("food_count", int, "整数", nullable=True)
        require_exact_type("device_model", str, "字符串", nullable=True)
        require_exact_type("food_size", int, "整数", nullable=True)

        if "quality" in annotations and annotations["quality"] is not None:
            quality = annotations["quality"]
            # JSON/JavaScript 不保留 215 与 215.0 的语义差异；后续规范化会
            # 始终转为 Python float，并由后端以 215.0 的形式落盘。
            if type(quality) not in {int, float}:
                raise ValueError("quality 必须是数字或 null。")

        for field_name in ("container_type", "accessory_type", "rack_level"):
            if field_name not in annotations:
                continue
            field_value = annotations[field_name]
            if not isinstance(field_value, list):
                raise ValueError(f"{field_name} 必须是字符串数组。")
            if not field_value:
                raise ValueError(f"{field_name} 不能为空数组，请使用 [\"无\"]。")
            if any(type(item) is not str for item in field_value):
                raise ValueError(f"{field_name} 必须是字符串数组。")

        return value

    @field_validator("revision")
    @classmethod
    def validate_revision(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("revision 不能为空。")
        return value
