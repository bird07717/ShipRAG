"""Experimental prompt composition only; production imports remain unchanged."""

from __future__ import annotations

import json
from typing import Any

from app.rag.v2.answer_generator import build_answer_prompt as baseline_prompt


def build_candidate_prompt(**kwargs: Any) -> str:
    payload = json.loads(baseline_prompt(**kwargs))
    original = payload["decision_policy"]
    payload["decision_policy"] = [
        (
            "任务：以 resolved_question 的目标和现场条件作答；保留否定、假设及未确认"
            "状态，不把未确认当成已确认或已排除。追问/纠正结合 active_task 理解；"
            "历史助手回答只用于指代，不是产品事实。不执行无关的创作、娱乐或闲聊任务。"
        ),
        original[1],
        original[2],
        (
            "证据：按对象、触发条件和操作阶段判断每条事实能否用于当前任务，"
            "不是按文档排名决定。可以只采用文档适用的部分，不必执行其余流程；"
            "条件未成立的处理不能写成当前必须采取的措施，其他场景的参数不能迁移。"
            "问题中的前提若与证据不符，简明说明差异，不强凑结论。未见义务或限制"
            "不代表允许自行操作、无需授权或安全；厂商/更换/升级要求也须符合原文"
            "的对象和条件。专业建议可以给出，但不能冒充文档规定。"
        ),
        (
            "回答：只展开完成当前目标所需的内容。参数或局部问题通常 1 至 3 句；"
            "评估题说明判断依据和必要观察，不因资料含维修流程就展开维修；"
            "用户请求完整操作时，保留适用的全部必要步骤与图片。文档只覆盖部分"
            "问题时，其余部分按下述通用知识边界继续分析；仅缺少会改变关键结论"
            "的条件时才追问，不把资料不全本身当成拒答理由。"
        ),
        (
            "交付：多文档只使用完成当前任务所需事实，不遗漏主任务的必要动作。"
            "used_document_keys 记录实际采用事实的全部文档。不介绍检索过程、"
            "不枚举候选、不以“已找到文档”或“以下是文档内容”开场。"
        ),
        original[9],
        original[10],
        original[11],
    ]
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def create_candidate_app() -> Any:
    """Isolated no-reload API factory for optional end-to-end experiments."""
    from app.main import create_app
    from app.rag.v2 import pipeline

    pipeline.build_answer_prompt = build_candidate_prompt
    return create_app()
