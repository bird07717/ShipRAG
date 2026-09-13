from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import Enum

from app.common.text import estimate_tokens

DEFAULT_PRODUCT_SCOPE = "本产品相关的问题，包括参数、功能、使用步骤、维护和故障排查"
POLICY_VERSION = "technical-assistant-v1"

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SPACE = re.compile(r"\s+")
_FULL_DOCUMENT = re.compile(
    r"全文|原文|完整(?:步骤|流程|文档|操作)?|全部(?:步骤|流程|内容)?|所有步骤"
)
_LOCAL_DETAIL = re.compile(
    r"哪里|在哪|哪个(?:位置|页面|菜单|按钮|接口)|这一项|该项|某一步|第[一二三四五六七八九十\d]+步|"
    r"多少|参数|数值|地址|端口|密码|开关|选项"
)
_EXPLANATION = re.compile(r"为什么|为何|原因|原理|区别|意味着|说明什么|影响|怎么理解|如何理解")
_TROUBLESHOOT = re.compile(
    r"故障|异常|失败|报错|告警|报警|无法|不能|没(?:有)?|无(?:法)?|不工作|断开|丢失|不通|卡住"
)
_GENERAL_PROCEDURE = re.compile(r"^(?:请|帮我|我要|想要|需要)?(?:给我)?(?:如何|怎么|怎样).{1,80}$")
_NEW_QUESTION = re.compile(r"[?？]|什么|如何|怎么|怎样|为什么|为何|哪里|在哪|多少|是否|能否|可以吗")
_CONDITION_REPLY = re.compile(
    r"^(?:是|不是|没有|有|已|已经|还没|尚未|仍然|依旧|会|不会|能|不能|确认|不确认|"
    r"型号|版本|设备|接口|端口|系统|Windows|Linux|PDC|VAU|VDR|DC\d+V)"
)
_TECH_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./:+-]{1,39}")


class AnswerStyle(str, Enum):
    FULL_DOCUMENT = "FULL_DOCUMENT"
    SHORT_ANSWER = "SHORT_ANSWER"
    EXPLANATION = "EXPLANATION"
    TROUBLESHOOT = "TROUBLESHOOT"


@dataclass(frozen=True, slots=True)
class AnswerPolicyDecision:
    style: AnswerStyle
    reason: str


@dataclass(frozen=True, slots=True)
class ScopeSnapshot:
    description: str
    sha256: str
    chars: int
    tokens: int
    truncated: bool


def classify_answer_style(question: str) -> AnswerPolicyDecision:
    """Classify output shape without an extra model call.

    Explicit full-document intent wins. Local details, explanations and
    troubleshooting then select bounded answer generation. A broad, complete
    procedure request such as “如何配置 VAU” keeps full-document delivery.
    """

    normalized = _SPACE.sub("", question)
    if _FULL_DOCUMENT.search(normalized):
        return AnswerPolicyDecision(AnswerStyle.FULL_DOCUMENT, "EXPLICIT_FULL_DOCUMENT")
    if _LOCAL_DETAIL.search(normalized):
        return AnswerPolicyDecision(AnswerStyle.SHORT_ANSWER, "LOCAL_DETAIL")
    if _EXPLANATION.search(normalized):
        return AnswerPolicyDecision(AnswerStyle.EXPLANATION, "EXPLANATION")
    if _TROUBLESHOOT.search(normalized):
        return AnswerPolicyDecision(AnswerStyle.TROUBLESHOOT, "TROUBLESHOOT")
    if _GENERAL_PROCEDURE.fullmatch(normalized):
        return AnswerPolicyDecision(AnswerStyle.FULL_DOCUMENT, "COMPLETE_PROCEDURE")
    return AnswerPolicyDecision(AnswerStyle.SHORT_ANSWER, "BOUNDED_DEFAULT")


def is_condition_reply(question: str) -> bool:
    normalized = question.strip()
    if not normalized or len(normalized) > 80 or _NEW_QUESTION.search(normalized):
        return False
    if _CONDITION_REPLY.search(normalized):
        return True
    if not _TECH_IDENTIFIER.fullmatch(normalized):
        return False
    return bool(
        any(character.isdigit() for character in normalized)
        or any(character.isupper() for character in normalized)
        or any(character in "_./:+-" for character in normalized)
    )


def escape_prompt_data(value: str) -> str:
    """Keep untrusted scope data inside its prompt delimiter."""

    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def normalize_scope_description(value: str | None) -> str:
    return _SPACE.sub(" ", _CONTROL.sub("", value or "")).strip()


def build_scope_snapshot(description: str | None, *, max_tokens: int) -> ScopeSnapshot:
    """Build a bounded, deterministic Prompt data snapshot.

    The public database value is not changed. Truncation operates on Unicode
    characters and repeatedly measures the same estimator used by Prompt
    budgets, avoiding byte-splitting or a second interpretation path.
    """

    public_scope = normalize_scope_description(description)
    combined = DEFAULT_PRODUCT_SCOPE
    if public_scope:
        combined += "；当前知识库公开范围：" + public_scope
    truncated = estimate_tokens(combined) > max_tokens
    if truncated:
        low, high = 0, len(combined)
        while low < high:
            middle = (low + high + 1) // 2
            if estimate_tokens(combined[:middle]) <= max_tokens:
                low = middle
            else:
                high = middle - 1
        combined = combined[:low].rstrip()
    return ScopeSnapshot(
        description=combined,
        sha256=hashlib.sha256(combined.encode("utf-8")).hexdigest(),
        chars=len(combined),
        tokens=estimate_tokens(combined),
        truncated=truncated,
    )


def resume_issue_query(question: str, chat_context: dict[str, object]) -> tuple[str, bool]:
    issue = chat_context.get("rag_issue")
    if not isinstance(issue, dict) or issue.get("version") != 1 or not is_condition_reply(question):
        return question, False
    origin = str(issue.get("origin_query") or "").strip()
    if not origin:
        return question, False
    return origin + "\n用户补充条件：" + question.strip(), True
