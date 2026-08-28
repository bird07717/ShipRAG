"""LLM generation for a single document Profile attribute."""
# ruff: noqa: E501

from __future__ import annotations

import asyncio
import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.core.config import Settings
from app.rag.llm import LlmError, LlmProvider, StructuredModel

_PROFILE_SYSTEM = """你是企业技术文档目录编辑。只依据单篇文档正文生成一个简洁、可审核的 Profile 草稿。
Profile 只包含三项：
1. canonical_question：用户会提出的一个标准问题；
2. aliases：不超过 5 个、确实等价的常见问法或术语；
3. routing_summary：一句话说明该文档处理什么问题、何时适用。

不得编造文档中没有的产品、型号、版本、错误码、步骤或适用范围。不要输出问题编码、关键词、产品型号、任务类型、症状、排除项、置信度、证据或审核结论。输出严格 JSON。"""

_PROFILE_SOURCE_MAX_CHARS = 6_000
_PROFILE_SIGNAL_PATTERN = re.compile(
    r"(?:[A-Za-z]+[-_ ]?\d+[A-Za-z0-9_-]*|"
    r"错误码|故障码|告警码|报警码|升级|更新|更换|替换|配置|设置|检查|检测|"
    r"无法|失败|异常|报警|告警|无响应|不适用|不支持|仅适用于|不能用于|禁止|请勿|注意)"
)


def build_profile_source(content: str, *, max_chars: int = _PROFILE_SOURCE_MAX_CHARS) -> str:
    """Select stable, representative text without adding another model call.

    Long technical documents often put error codes, limitations and warnings
    after the introductory section. Keep the beginning and end, then use the
    remaining budget for signal-bearing lines from the whole document.
    """
    clean = content.strip()
    if len(clean) <= max_chars:
        return clean

    head_budget = max_chars // 2
    tail_budget = max_chars // 5
    signal_budget = max_chars - head_budget - tail_budget - 4
    head = clean[:head_budget].rstrip()
    tail = clean[-tail_budget:].lstrip()
    selected: list[str] = []
    seen: set[str] = set()
    used = 0
    for raw_line in clean.splitlines():
        line = raw_line.strip()
        if not line or not _PROFILE_SIGNAL_PATTERN.search(line):
            continue
        normalized = re.sub(r"\s+", " ", line)
        if normalized in seen or normalized in head or normalized in tail:
            continue
        required = len(normalized) + (1 if selected else 0)
        if used + required > signal_budget:
            continue
        selected.append(normalized)
        seen.add(normalized)
        used += required
    segments = [head]
    if selected:
        segments.append("\n".join(selected))
    segments.append(tail)
    return "\n\n".join(segment for segment in segments if segment)[:max_chars]


class GeneratedProfile(BaseModel):
    canonical_question: str = Field(min_length=1, max_length=500)
    aliases: list[str] = Field(default_factory=list, max_length=5)
    routing_summary: str = Field(default="", max_length=500)

    @field_validator("aliases")
    @classmethod
    def clean_items(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))


class DocumentProfileGenerator:
    # A single short retry bounds the delay of a whole knowledge-base run.
    _RETRY_BACKOFF_SECONDS = (2.0,)

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def _complete_structured(
        self,
        provider: LlmProvider,
        prompt: str,
        *,
        system: str,
        schema: type[StructuredModel],
        max_tokens: int,
    ) -> StructuredModel:
        """Run the one Profile draft call with one bounded transient retry."""
        attempts = max(1, self.settings.profile_llm_retry_attempts)
        for attempt in range(attempts):
            try:
                return await provider.complete_structured(
                    prompt,
                    system=system,
                    schema=schema,
                    max_tokens=max_tokens,
                    temperature=0,
                    timeout_seconds=self.settings.profile_llm_timeout_seconds,
                )
            except LlmError as exc:
                if not exc.retryable or attempt >= attempts - 1:
                    raise
                delay = self._RETRY_BACKOFF_SECONDS[
                    min(attempt, len(self._RETRY_BACKOFF_SECONDS) - 1)
                ]
                await asyncio.sleep(delay)
        raise AssertionError("unreachable")

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
        source = build_profile_source(str(document.get("content") or ""))
        prompt = f"文档标题：{title}\n\n文档正文：\n{source}"
        try:
            generated = await self._complete_structured(
                provider,
                prompt,
                system=_PROFILE_SYSTEM,
                schema=GeneratedProfile,
                max_tokens=600,
            )
        except LlmError as exc:
            return (
                fallback,
                "FALLBACK",
                exc.code,
                "FALLBACK",
                [f"Profile 草稿生成失败：{exc.code}"],
            )
        return generated, "LLM", None, "PASS", []

    @staticmethod
    def _fallback(title: str, document: dict[str, Any]) -> GeneratedProfile:
        return GeneratedProfile(
            canonical_question=title.rstrip("？?。 ") + "？",
            aliases=[],
            routing_summary=f"《{title}》的待人工确认 Profile 草稿。",
        )
