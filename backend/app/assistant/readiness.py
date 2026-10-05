"""Actionable management checks; existing runtime blockers remain authoritative."""

from typing import Any


class ReadinessBlockers(list[str]):
    def __init__(self) -> None:
        super().__init__()
        self.groups: dict[str, list[str]] = {}

    def add(self, group: str, message: str) -> None:
        self.append(message)
        self.groups.setdefault(group, []).append(message)


def publication_checks(blockers: list[str], groups: dict[str, list[str]]) -> list[dict[str, Any]]:
    definitions = (
        ("KNOWLEDGE", "知识库启用状态", "knowledge", "", "查看知识库"),
        ("MODELS", "问答模型配置", "configuration", "tab=models", "配置模型"),
        ("INDEX", "当前 RAG 索引", "indexes", "", "检查索引"),
        ("UNDERSTANDING", "文档理解人工确认", "documents", "", "检查文档理解"),
        ("POLICY", "执行策略运行条件", "engine-routing", "", "检查执行策略"),
    )
    known = {message for messages in groups.values() for message in messages}
    unknown = [message for message in blockers if message not in known]
    checks = [
        {
            "code": code,
            "title": title,
            "status": "BLOCKED" if groups.get(code) else "PASSED",
            "messages": groups.get(code, []),
            "action": {"view": view, "query": query, "label": label},
        }
        for code, title, view, query, label in definitions
    ]
    if unknown:
        # Compatibility/unknown reasons must never be silently classified as ready.
        checks.append(
            {
                "code": "OTHER",
                "title": "其他运行条件需要检查",
                "status": "BLOCKED",
                "messages": unknown,
                "action": {"view": "engine-routing", "query": "", "label": "检查运行条件"},
            }
        )
    return checks
