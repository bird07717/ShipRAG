from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.document_profiles.generator import GeneratedProfile, build_profile_source


def test_profile_source_keeps_tail_and_signal_lines_from_long_document() -> None:
    head = "文档开头" * 2_000
    middle = "普通说明\n" * 3_000
    signal = "注意：VDR600 出现 ERR-204 告警时不适用于网口升级。\n"
    tail = "文档结尾" * 1_000
    source = build_profile_source(head + middle + signal + tail, max_chars=2_000)

    assert len(source) <= 2_000
    assert source.startswith("文档开头")
    assert "VDR600 出现 ERR-204 告警" in source
    assert source.endswith("文档结尾" * 50)


def test_profile_source_keeps_short_document_unchanged() -> None:
    assert build_profile_source("  简短文档  ") == "简短文档"


def test_generated_profile_keeps_only_routing_content_and_normalizes_aliases() -> None:
    profile = GeneratedProfile(
        canonical_question="如何升级 PDC？",
        aliases=[" PDC 怎么升级 ", "PDC 怎么升级", ""],
        routing_summary="PDC 固件升级的操作说明。",
    )

    assert profile.aliases == ["PDC 怎么升级"]
    assert profile.routing_summary == "PDC 固件升级的操作说明。"


def test_generated_profile_rejects_more_than_five_aliases() -> None:
    with pytest.raises(ValidationError):
        GeneratedProfile(
            canonical_question="如何升级 PDC？",
            aliases=[f"别名 {index}" for index in range(6)],
        )
