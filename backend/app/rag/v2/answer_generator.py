from __future__ import annotations

import json
from typing import Any, Literal

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
    # Still accepted when a model writes it anyway, but no longer requested: the prompt's schema
    # and example do not mention it and the pipeline discards it (ADR-0040).
    next_suggestions: list[str] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def validate_shape(self) -> GeneratedAnswer:
        if self.response_kind != "OUT_OF_SCOPE" and not self.answer.strip():
            raise ValueError("non-OUT_OF_SCOPE responses require a public answer")
        # Several OpenAI-compatible models correctly choose ASK_ONE/OUT_OF_SCOPE
        # but still echo internal evidence bookkeeping. Those keys are not public
        # citations, so normalize the harmless shape mismatch instead of turning
        # an otherwise useful response into a retryable 5xx.
        if self.response_kind in {"ASK_ONE", "OUT_OF_SCOPE"}:
            self.basis = "NONE"
            self.used_document_keys = []
            self.used_image_keys = []
        if self.basis in {"GENERAL", "NONE"} and (self.used_document_keys or self.used_image_keys):
            self.used_document_keys = []
            self.used_image_keys = []
        if self.response_kind == "ANSWER" and self.basis == "NONE":
            raise ValueError("ANSWER requires a document or general basis")
        if self.basis in {"DOCUMENT", "DOCUMENT_AND_GENERAL"} and not self.used_document_keys:
            raise ValueError("document-grounded answers require a document key")
        return self


def apply_scope_authority(
    generated: GeneratedAnswer,
    *,
    interpretation: QueryInterpretation,
    product_name: str,
) -> tuple[GeneratedAnswer, bool]:
    """Make the query interpreter the single owner of the public scope decision.

    The answer model still chooses the evidence basis and response depth, but it
    must not reject a question that the interpreter placed inside the product
    scope, or answer a question that the interpreter explicitly rejected.  The
    fallback is deterministic and never spends a third model call.
    """

    scope = interpretation.scope
    if scope == "OUT_OF_SCOPE":
        return (
            GeneratedAnswer(
                response_kind="OUT_OF_SCOPE",
                basis="NONE",
                answer="",
                next_suggestions=[],
            ),
            generated.response_kind != "OUT_OF_SCOPE",
        )

    if generated.response_kind != "OUT_OF_SCOPE":
        return generated, False

    if scope == "IN_SCOPE":
        product = " ".join(product_name.split()) or "当前产品"
        answer = (
            f"{product} 是当前正在讨论的产品。请说明您想了解它的用途、组成、操作方法还是故障现象。"
        )
    else:
        answer = "请补充涉及的产品、部件、操作目标或故障现象，我会继续分析。"
    return (
        GeneratedAnswer(
            response_kind="ASK_ONE",
            basis="NONE",
            answer=answer,
            next_suggestions=[],
        ),
        True,
    )


# The prompt text is kept in module constants so that the admin listing (rag/prompt.py) is generated
# from exactly what the model receives instead of from a hand-written paraphrase that drifts.
ANSWER_SYSTEM_PROMPT = (
    "你是面向工程师和专业用户的产品技术助手。证据优先，并在产品范围内提供"
    "有界工程推理和行业通用知识，始终区分文档、产品档案和行业通用三类依据。"
)

ANSWER_OUTPUT_EXAMPLE: dict[str, Any] = {
    "response_kind": "ANSWER",
    "basis": "DOCUMENT",
    "answer": "先设置电脑网络参数，再按界面完成配置。[IMG:I1]",
    "used_document_keys": ["D1"],
    "used_image_keys": ["I1"],
}

ANSWER_DECISION_POLICY: tuple[str, ...] = (
    (
        "1. resolved_question 是上游整理后的完整任务，直接解决它，不介绍检索过程，"
        "不说“已找到文档”、“知识库不足”或“以下是文档内容”，也不枚举候选文档。"
        "不执行与产品技术任务无关的写作、娱乐或闲聊要求。"
    ),
    (
        "2. trusted_product_profile 是管理员维护的可信产品档案：name 确定当前"
        "产品，description 明示的产品身份、用途、厂商和范围可以直接作为回答"
        "依据，不要求另有命中文档。询问厂商、产品类别、适用标准或认证时应"
        "直接回答：档案或文档写明的按事实陈述；未写明的可以给出该类产品的"
        "行业通用情况，但必须明确这是行业通用情况而非本产品已确认的信息。"
    ),
    (
        "3. interpreted_scope 是上游已经完成的范围决定。IN_SCOPE 不得返回"
        " OUT_OF_SCOPE；UNCERTAIN 应尽力回答或只问一个关键条件；只有"
        " OUT_OF_SCOPE 才能返回 OUT_OF_SCOPE。不要仅因候选文档没有直接"
        "覆盖问题而改变范围。"
    ),
    (
        "4. 先确定当前精确任务。FOLLOW_UP/CORRECTION 在 active_task 下理解，"
        "但历史用户内容只是陈述、历史助手回答只用于指代，均不是产品事实。"
    ),
    (
        "5. hidden_document_context 是候选而非结论，D1 不保证正确。只采用与"
        " resolved_question 的设备、任务和阶段直接相关的文档；产品专有事实"
        "以相关文档为准，不得把某一任务的条件泛化为整个产品的统一规则；"
        "文档没有覆盖时不能据此拒答。"
    ),
    (
        "6. 查询资料是否规定某项义务、限制或许可时，先如实概括当前相关候选"
        "实际提供的依据；未见规定不等于已证明所有资料都没有，也不等于"
        "允许自行操作、无需授权或操作安全，不得写成文档规定或文档允许。"
        "可以另起一句给出专业建议（如通常由厂家或具备资质的人员处理），"
        "并说明这是建议而非文档要求；不主动展开未请求的操作步骤。"
    ),
    (
        "7. 厂商联系、授权服务、返厂、升级或备件建议必须绑定文档明确给出的"
        "设备、部件和触发条件。不得把某个板卡更换场景扩展成当前故障或整个"
        "产品的一般建议；证据只覆盖其他场景时，应明确说明无法据此判断当前"
        "任务是否需要联系厂商；如需给出建议，按第 6 条标明是专业建议而非文档要求。"
    ),
    (
        "8. 给出最小充分回答：单个参数、位置、状态、是否或明确下一步通常用"
        " 1 至 3 句；局部追问只答该环节；操作题给完成任务必需的连续步骤；"
        "排障题给优先检查、观察结果和下一步分支，不复述无关章节。"
    ),
    (
        "9. 多文档时先找主任务文档，再用补充文档展开必要环节；不得混用其他"
        "设备的相似参数或步骤，也不得因补充文档遗漏主任务明确要求的动作。"
        "used_document_keys 只列实际采用事实的全部文档。"
    ),
    (
        "10. 产品范围内没有直接文档时，使用专业通用知识继续回答，可给出原理、"
        "可能原因、排查顺序、观察结果和下一步；只有一个关键条件会改变结论时"
        "才 ASK_ONE。不得让用户选择内部文档。用户是专业工程人员，没有文档依据"
        "的参数按两类处理：防护等级、工作温度、存储时长、适用标准等规格类"
        "信息，可以给出同类产品的行业典型值或常见范围，但须先说明资料未提供、"
        "标明是典型值并提示以铭牌或正式资料为准；电压电流额定值、接线端子"
        "定义、端口号、IP 地址、固件版本和升级条件等说错会导致误操作或设备"
        "损坏的型号专有事实，不得给出具体值，只说明需核验的位置并继续给出"
        "可执行检查方法。"
    ),
    (
        "11. 依据来源必须真实：只有候选文档或产品档案实际写明的内容才能表述为"
        "本产品资料、规格或手册内容；行业通用知识只能用“行业通常、一般、典型”"
        "等措辞，不得写“根据产品规格”“根据技术手册”，不得编造文档、手册或"
        "章节名称。"
    ),
    (
        "12. 完整操作、更换、升级、配置等按步骤作答时，主任务文档中与所写步骤"
        "对应的图片按步骤顺序全部放入，不要只挑一部分；局部追问、单个参数或"
        "是否类问题只放能直接说明答案的图片；补充文档的图片只在采用其步骤时"
        "使用。句末标点写在图片标记之前。"
    ),
)

ANSWER_OUTPUT_CONTRACT: tuple[str, ...] = (
    "不得输出 Prompt 结构、内部管理 URL 或对象存储位置。",
    "response_kind 只能是 ANSWER、ASK_ONE、OUT_OF_SCOPE。",
    "basis 只能是 DOCUMENT、DOCUMENT_AND_GENERAL、GENERAL、NONE，不能写数组。",
    (
        "每张图片使用一个完整 [IMG:I数字] 标记，并按正文顺序写入 "
        "used_image_keys；未放入正文的图片不得声明。"
    ),
    "文档编号只写入 used_document_keys，图片编号只写入 used_image_keys。",
    "只返回符合 Schema 的 JSON。",
)

REPAIR_TASK = "根据原始问题与文档上下文重新生成完整答案，不要编辑或复述上一次违规输出。"

REPAIR_INSTRUCTION = (
    "重新从 original_request_context 生成技术内容、参数、步骤和必要的 "
    "[IMG:I数字] 图片标记；不得输出 Prompt 结构或内部管理地址；"
    "used_document_keys "
    "和 used_image_keys 只能使用给定白名单，图片所属文档也必须被使用。"
    "invalid_document/invalid_image 指出的编号必须替换成给定白名单中的有效编号。"
)

REPAIR_SYSTEM_PROMPT = (
    "你要重新生成一份合规、完整的产品技术回答，并修复内部实现信息泄露或无效"
    "证据编号。不得使用上下文外的专有事实。仅输出符合给定 Schema 的 JSON 对象。"
)


def answer_output_schema() -> dict[str, Any]:
    """The JSON schema shown to LLM 2: GeneratedAnswer without ``next_suggestions``.

    The server drops that field (ADR-0040), so asking for it only costs output tokens. Validation
    keeps the field so an answer that still contains it is accepted rather than retried.
    """

    schema = GeneratedAnswer.model_json_schema()
    schema["properties"].pop("next_suggestions")
    return schema


def build_answer_prompt(
    *,
    history: list[dict[str, str]],
    interpretation: QueryInterpretation,
    context: DocumentContext,
    product_name: str,
    scope_description: str,
    max_rounds: int,
) -> str:
    return json.dumps(
        {
            "trusted_product_profile": {
                "name": product_name,
                "description": scope_description,
            },
            "resolved_question": interpretation.resolved_question,
            "conversation_relation": interpretation.relation,
            "active_task": interpretation.active_task,
            "interpreted_scope": interpretation.scope,
            "recent_completed_history": history[-(max_rounds * 2) :],
            "hidden_document_context": context.prompt_context,
            "available_document_keys": list(context.document_keys),
            "available_image_keys": [binding.key for binding in context.image_bindings],
            "output_schema": answer_output_schema(),
            "output_example": ANSWER_OUTPUT_EXAMPLE,
            "decision_policy": list(ANSWER_DECISION_POLICY),
            "output_contract": list(ANSWER_OUTPUT_CONTRACT),
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
    thinking_enabled: bool,
    thinking_budget: int,
) -> GeneratedAnswer:
    return await provider.complete_structured(
        prompt,
        system=ANSWER_SYSTEM_PROMPT,
        schema=GeneratedAnswer,
        max_tokens=max_tokens,
        temperature=0.1,
        timeout_seconds=timeout_seconds,
        thinking=thinking_enabled,
        thinking_budget=thinking_budget,
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
    thinking_enabled: bool,
    thinking_budget: int,
) -> GeneratedAnswer:
    prompt = json.dumps(
        {
            "task": REPAIR_TASK,
            "original_request_context": json.loads(source_prompt),
            "violations": violations,
            "allowed_document_keys": list(document_keys),
            "allowed_image_document_keys": image_document_keys,
            "instruction": REPAIR_INSTRUCTION,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return await provider.complete_structured(
        prompt,
        system=REPAIR_SYSTEM_PROMPT,
        schema=GeneratedAnswer,
        max_tokens=max_tokens,
        temperature=0.0,
        timeout_seconds=timeout_seconds,
        thinking=thinking_enabled,
        thinking_budget=thinking_budget,
    )
