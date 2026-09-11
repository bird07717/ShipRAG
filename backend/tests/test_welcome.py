"""Unit tests for the demo-chat welcome message builder."""

from __future__ import annotations

from uuid import uuid4

from app.rag.welcome import build_assistant_welcome, build_suggestions, build_welcome


def _kb(**overrides):
    base = {
        "id": uuid4(),
        "name": "VDR 产品知识库",
        "description": None,
        "status": "ENABLED",
        "active_index_id": uuid4(),
    }
    base.update(overrides)
    return base


def test_welcome_ready_with_catalog() -> None:
    payload = build_welcome(
        _kb(),
        [
            {"document_id": "a", "title": "故障解决文档11—如何U盘升级PDC"},
            {"document_id": "b", "title": "故障解决文档13—FFC 如何检查接线盒"},
        ],
    )
    assert payload["knowledge_base"]["ready"] is True
    assert "VDR 产品知识库" in payload["message"]
    assert "2 份文档" in payload["message"]
    assert payload["suggestions"] == ["如何U盘升级PDC？", "FFC 如何检查接线盒？"]


def test_welcome_includes_description_when_present() -> None:
    payload = build_welcome(
        _kb(description="覆盖VDR设备的安装、维护与故障排查资料"),
        [{"document_id": "a", "title": "手册"}],
    )
    assert "覆盖VDR设备的安装、维护与故障排查资料" in payload["message"]


def test_assistant_welcome_uses_verified_golden_questions() -> None:
    payload = build_assistant_welcome(
        _kb(description="覆盖VDR设备的安装、维护与故障排查资料"),
        [
            {"document_id": "a", "title": "故障解决文档11—如何U盘升级PDC"},
            {"document_id": "b", "title": "故障解决文档13—FFC 如何检查接线盒"},
        ],
    )

    assert payload["message"] == (
        "您好，我是您的智能技术助手。请描述您遇到的问题或想完成的操作，"
        "我会为您提供清晰、可执行的建议。"
    )
    assert "2 份文档" not in payload["message"]
    assert "VDR 产品知识库" not in payload["message"]
    assert "覆盖VDR设备的安装、维护与故障排查资料" not in payload["message"]
    assert payload["suggestions"] == [
        "如何使用U盘升级PDC",
        "如何更换PSU电源板",
        "如何手动自检VDR",
    ]


def test_assistant_welcome_never_exposes_admin_setup_guidance() -> None:
    payload = build_assistant_welcome(_kb(active_index_id=None), [{"title": "手册"}])

    assert payload["message"] == "服务正在准备中，请稍后再试。"
    assert payload["suggestions"] == []


def test_welcome_without_documents_guides_upload() -> None:
    payload = build_welcome(_kb(), [])
    assert payload["knowledge_base"]["ready"] is False
    assert "还没有收录文档" in payload["message"]
    assert payload["suggestions"] == []


def test_welcome_without_active_index_guides_build() -> None:
    payload = build_welcome(_kb(active_index_id=None), [{"document_id": "a", "title": "手册"}])
    assert payload["knowledge_base"]["ready"] is False
    assert "还没有可用的文档索引" in payload["message"]


def test_welcome_disabled_kb_reports_disabled() -> None:
    payload = build_welcome(_kb(status="DISABLED"), [{"document_id": "a", "title": "手册"}])
    assert payload["knowledge_base"]["ready"] is False
    assert "已停用" in payload["message"]
    assert payload["suggestions"] == []


def test_suggestions_strip_prefixes_and_skip_short_titles() -> None:
    catalog = [
        {"document_id": "a", "title": "故障解决文档13—FFC 如何检查接线盒"},
        {"document_id": "b", "title": "快速入门"},
        {"document_id": "c", "title": "故障解决文档10—如何更换故障PDC"},
        {"document_id": "d", "title": "故障解决文档9— telnet"},
        {"document_id": "e", "title": "故障解决文档1—如何配置系统参数"},
    ]
    suggestions = build_suggestions(catalog)
    assert suggestions == [
        "FFC 如何检查接线盒？",
        "快速入门？",
        "如何更换故障PDC？",
    ]


def test_suggestions_skip_unparseable_titles() -> None:
    assert build_suggestions([{"document_id": "a", "title": ""}]) == []
    assert build_suggestions([{"document_id": "a", "title": "ab"}]) == []
