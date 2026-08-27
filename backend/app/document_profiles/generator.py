"""LLM generation for a single document Profile attribute."""
# ruff: noqa: E501

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.core.config import Settings
from app.rag.llm import LlmError, LlmProvider

_SCOPE_SYSTEM = """你是企业技术文档范围提取器。只依据单篇文档正文（标题仅作辅助），
识别该文档实际解决的一个核心对象和任务。列出正文可验证的症状、明确不适用范围，以及支持这些结论的短摘录。输出严格 JSON。"""
_PROFILE_SYSTEM = """你是企业技术文档目录编辑。根据给定的文档范围和正文生成一个可审核的问题 Profile 草稿。
只描述范围内的一个核心问题；不得编造文档没有的产品、型号、版本、错误码或步骤。输出严格 JSON。"""
_REVIEW_SYSTEM = """你是企业技术文档 Profile 质量审核员。核对 Profile 的问题主体、别名、关键词、症状和排除范围是否都有文档证据。输出修订后的 Profile、质量状态、简短原因和证据，严格 JSON。"""


class ProfileEvidence(BaseModel):
    field: str = Field(min_length=1, max_length=80)
    excerpt: str = Field(min_length=1, max_length=1_000)
    element_hint: str | None = Field(default=None, max_length=120)


class ProfileScope(BaseModel):
    core_subject: str = Field(min_length=1, max_length=300)
    task: str | None = Field(default=None, max_length=300)
    symptoms: list[str] = Field(default_factory=list, max_length=12)
    exclusions: list[str] = Field(default_factory=list, max_length=8)
    evidence: list[ProfileEvidence] = Field(default_factory=list, max_length=12)


class GeneratedProfile(BaseModel):
    problem_code: str = Field(min_length=1, max_length=100)
    canonical_question: str = Field(min_length=1, max_length=500)
    aliases: list[str] = Field(default_factory=list, max_length=10)
    keywords: list[str] = Field(default_factory=list, max_length=12)
    product_name: str | None = Field(default=None, max_length=200)
    model_names: list[str] = Field(default_factory=list, max_length=10)
    task_type: str | None = Field(default=None, max_length=50)
    routing_summary: str = Field(default="", max_length=1_000)
    symptoms: list[str] = Field(default_factory=list, max_length=12)
    exclusions: list[str] = Field(default_factory=list, max_length=8)
    confidence: float = Field(default=0.5, ge=0, le=1)

    @field_validator("aliases", "keywords", "model_names", "symptoms", "exclusions")
    @classmethod
    def clean_items(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))


class ProfileQualityReview(BaseModel):
    profile: GeneratedProfile
    quality_status: str
    quality_notes: list[str] = Field(default_factory=list, max_length=12)
    evidence: list[ProfileEvidence] = Field(default_factory=list, max_length=16)


class DocumentProfileGenerator:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def generate(
        self, document: dict[str, Any], provider: LlmProvider | None
    ) -> tuple[GeneratedProfile, str, str | None, str, list[str]]:
        title = str(document["display_name"] or document["filename"]).strip()
        fallback = self._fallback(title, document)
        if provider is None:
            return (
                fallback,
                "FALLBACK",
                "MODEL_NOT_CONFIGURED",
                "FALLBACK",
                ["模型不可用，需人工审核。"],
            )
        prompt = f"文档标题：{title}\n\n文档正文：\n{str(document.get('content') or '')[:20_000]}"
        try:
            scope = await provider.complete_structured(
                prompt,
                system=_SCOPE_SYSTEM,
                schema=ProfileScope,
                max_tokens=800,
                temperature=0,
                timeout_seconds=self.settings.m3_provider_timeout_seconds,
            )
            generated = await provider.complete_structured(
                f"{prompt}\n\n文档范围：\n{scope.model_dump_json()}",
                system=_PROFILE_SYSTEM,
                schema=GeneratedProfile,
                max_tokens=1_024,
                temperature=0,
                timeout_seconds=self.settings.m3_provider_timeout_seconds,
            )
        except LlmError as exc:
            return (
                fallback,
                "FALLBACK",
                exc.code,
                "FALLBACK",
                [f"离线范围提取或生成失败：{exc.code}"],
            )
        try:
            review = await provider.complete_structured(
                f"{prompt}\n\n文档范围：\n{scope.model_dump_json()}\n\n待审核 Profile：\n{generated.model_dump_json()}",
                system=_REVIEW_SYSTEM,
                schema=ProfileQualityReview,
                max_tokens=1_024,
                temperature=0,
                timeout_seconds=self.settings.m3_provider_timeout_seconds,
            )
        except LlmError as exc:
            return generated, "LLM", exc.code, "REVIEW_REQUIRED", [f"离线质量复核失败：{exc.code}"]
        return review.profile, "LLM", None, review.quality_status, review.quality_notes

    @staticmethod
    def _fallback(title: str, document: dict[str, Any]) -> GeneratedProfile:
        words = re.findall(r"[A-Za-z][A-Za-z0-9_-]{1,30}", title)
        code = "_".join(word.upper().replace("-", "_") for word in words[:5]).strip("_")
        code = code or f"DOC_{str(document['document_id']).replace('-', '')[:8].upper()}"
        task_type = next(
            (
                value
                for token, value in (
                    ("升级", "UPGRADE"),
                    ("更换", "REPLACEMENT"),
                    ("检查", "CHECK"),
                    ("配置", "CONFIGURATION"),
                    ("设置", "CONFIGURATION"),
                    ("报警", "ALARM"),
                )
                if token in title
            ),
            None,
        )
        return GeneratedProfile(
            problem_code=code,
            canonical_question=title.rstrip("？?。 ") + "？",
            keywords=[
                term
                for term in ("升级", "更换", "检查", "配置", "设置", "报警", "故障")
                if term in title
            ],
            model_names=[word.upper() for word in words if any(char.isdigit() for char in word)][
                :5
            ],
            task_type=task_type,
            routing_summary=f"《{title}》的保守路由摘要：需由管理员根据正文确认。",
            confidence=0.35,
        )
