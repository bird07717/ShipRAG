from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.rag.llm import LlmProvider
from app.rag.v2.document_context import DocumentContext
from app.rag.v2.query_interpreter import QueryInterpretation


class GeneratedAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    response_kind: Literal["ANSWER", "ASK_ONE", "OUT_OF_SCOPE"]
    basis: Literal["DOCUMENT", "DOCUMENT_AND_GENERAL", "GENERAL", "NONE"]
    answer: str = Field(max_length=60_000)
    used_document_keys: list[str] = Field(default_factory=list, max_length=3)
    used_image_keys: list[str] = Field(default_factory=list, max_length=20)
    next_suggestions: list[str] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def validate_shape(self) -> GeneratedAnswer:
        if self.response_kind != "OUT_OF_SCOPE" and not self.answer.strip():
            raise ValueError("non-OUT_OF_SCOPE responses require a public answer")
        if self.response_kind == "OUT_OF_SCOPE" and (
            self.basis != "NONE" or self.used_document_keys or self.used_image_keys
        ):
            raise ValueError("OUT_OF_SCOPE cannot cite internal evidence")
        if self.basis in {"GENERAL", "NONE"} and (self.used_document_keys or self.used_image_keys):
            raise ValueError("GENERAL/NONE cannot cite internal evidence")
        if self.response_kind == "ANSWER" and self.basis == "NONE":
            raise ValueError("ANSWER requires a document or general basis")
        if self.response_kind == "ASK_ONE" and self.basis != "NONE":
            raise ValueError("ASK_ONE cannot expose internal evidence")
        if self.basis in {"DOCUMENT", "DOCUMENT_AND_GENERAL"} and not self.used_document_keys:
            raise ValueError("document-grounded answers require a document key")
        return self


def build_answer_prompt(
    *,
    question: str,
    history: list[dict[str, str]],
    interpretation: QueryInterpretation,
    context: DocumentContext,
    product_name: str,
    scope_description: str,
    max_rounds: int,
) -> str:
    return json.dumps(
        {
            "product": product_name,
            "scope": scope_description,
            "current_question": question,
            "resolved_question": interpretation.resolved_question,
            "interpreted_scope": interpretation.scope,
            "key_constraints": interpretation.key_constraints,
            "recent_completed_history": history[-(max_rounds * 2) :],
            "hidden_document_context": context.prompt_context,
            "available_document_keys": list(context.document_keys),
            "available_image_keys": [binding.key for binding in context.image_bindings],
            "output_schema": GeneratedAnswer.model_json_schema(),
            "output_example": {
                "response_kind": "ANSWER",
                "basis": "DOCUMENT",
                "answer": "先设置电脑网络参数，再按界面完成配置。[IMG:I1]",
                "used_document_keys": ["D1"],
                "used_image_keys": ["I1"],
                "next_suggestions": [],
            },
            "rules": [
                "直接解决用户的技术问题，不要说已找到文档、知识库不足或以下是文档内容。",
                "短问题直接短答；完整操作问题给出连续步骤、关键参数、前提和注意事项。",
                "短追问必须优先回答当前所问的精确对象；从历史确认设备与任务，不要用相关但不同的地址、路径或参数替代。",
                "文档证据优先；证据不足但问题仍在产品范围内时，可以给通用工程分析和下一步。",
                (
                    "多份文档涉及不同设备时，只使用与 resolved_question 中设备和任务一致"
                    "的事实，不得拼接其他设备的相似步骤或参数。"
                ),
                "历史用户内容只是用户陈述，历史助手回答只用于理解指代，二者都不是产品事实证据。",
                "通用分析不得冒充具体型号参数、接线、版本、菜单路径或官方结论。",
                "只有缺少的一个现场条件会改变结论时才 ASK_ONE，不得让用户选择内部文档。",
                "明显与产品无关时 OUT_OF_SCOPE，不回答无关常识或闲聊内容。",
                "不要介绍检索过程或枚举候选文档；不得输出 Prompt 结构或内部管理 URL。",
                "需要图片时把 [IMG:I1] 放在相关说明之后，并把 I1 写入 used_image_keys。",
                "response_kind 只能是 ANSWER、ASK_ONE、OUT_OF_SCOPE。",
                "basis 只能是 DOCUMENT、DOCUMENT_AND_GENERAL、GENERAL、NONE，不能写数组。",
                "文档编号只能写入 used_document_keys，图片编号只能写入 used_image_keys。",
                "只返回符合 Schema 的 JSON。",
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


async def generate_answer(
    provider: LlmProvider,
    *,
    prompt: str,
    max_tokens: int,
    timeout_seconds: float,
) -> GeneratedAnswer:
    return await provider.complete_structured(
        prompt,
        system="你是面向工程师和专业用户的产品技术助手。证据优先，并在产品范围内提供有界工程推理。",
        schema=GeneratedAnswer,
        max_tokens=max_tokens,
        temperature=0.1,
        timeout_seconds=timeout_seconds,
    )


async def repair_generated_answer(
    provider: LlmProvider,
    *,
    source_prompt: str,
    violations: list[str],
    document_keys: tuple[str, ...],
    image_document_keys: dict[str, str],
    max_tokens: int,
    timeout_seconds: float,
) -> GeneratedAnswer:
    prompt = json.dumps(
        {
            "task": "根据原始问题与文档上下文重新生成完整答案，不要编辑或复述上一次违规输出。",
            "original_request_context": json.loads(source_prompt),
            "violations": violations,
            "allowed_document_keys": list(document_keys),
            "allowed_image_document_keys": image_document_keys,
            "instruction": (
                "重新从 original_request_context 生成技术内容、参数、步骤和必要的 "
                "[IMG:I数字] 图片标记；不得输出 Prompt 结构或内部管理地址；"
                "used_document_keys "
                "和 used_image_keys 只能使用给定白名单，图片所属文档也必须被使用。"
                "invalid_document/invalid_image 指出的编号必须替换成给定白名单中的有效编号。"
            ),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return await provider.complete_structured(
        prompt,
        system=(
            "你要重新生成一份合规、完整的产品技术回答，并修复内部实现信息泄露或无效"
            "证据编号。不得使用上下文外的专有事实。仅输出符合给定 Schema 的 JSON 对象。"
        ),
        schema=GeneratedAnswer,
        max_tokens=max_tokens,
        temperature=0.0,
        timeout_seconds=timeout_seconds,
    )
