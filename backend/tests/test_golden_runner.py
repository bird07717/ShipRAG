from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import httpx
import pytest


def _runner() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "rag-acceptance" / "run_golden.py"
    spec = importlib.util.spec_from_file_location("rag_golden_runner", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_assistant_mode_sends_public_contract_headers_and_reads_complete() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["body"] = request.content.decode()
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content=(
                'event: accepted\ndata: {"request_id":"golden-request"}\n\n'
                ": ping\n\n"
                'event: delta\ndata: {"text":"回答"}\n\n'
                'event: complete\ndata: {"conversation_id":"conversation-1",'
                '"message_id":"message-1","answer":"回答","references":[]}\n\n'
            ).encode(),
        )

    runner = _runner()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "Bearer assistant-token"},
    ) as client:
        payload, _latency, error = await runner.run_turn(
            client,
            base_url="http://assistant.test",
            kb_id="00000000-0000-0000-0000-000000000001",
            question="测试问题",
            conversation_id=None,
            request_id="golden-request",
            api_mode="assistant",
            external_user_id="golden-user",
        )

    assert error is None
    assert payload is not None and payload["answer"] == "回答"
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers["authorization"] == "Bearer assistant-token"
    assert headers["x-external-user-id"] == "golden-user"
    assert headers["x-request-id"] == "golden-request"
    assert headers["idempotency-key"] == "golden-request"
    assert '"request_id"' not in str(captured["body"])


def test_assistant_public_references_are_evaluated_without_admin_catalog() -> None:
    runner = _runner()
    hits = runner.extract_hits(
        {
            "references": [{"title": "如何使用U盘升级PDC"}],
            "answer": "请参考《如何使用U盘升级PDC》。",
        },
        {},
    )
    passed, problems = runner.evaluate(
        {
            "document_keys": ["U盘升级PDC"],
            "forbidden_keys": ["升级3359核心板"],
            "tolerance": "EXACT",
        },
        hits,
    )

    assert passed is True
    assert problems == []


def test_v2_hidden_trace_evidence_replaces_delivery_contract_assertions() -> None:
    runner = _runner()
    expected = runner.v2_expected(
        {
            "response_types": ["DOC_DELIVERED", "DOCUMENT_DELIVERED"],
            "document_keys": ["VAU图像"],
            "require_reference": True,
        }
    )
    is_v2, hits, problems = runner.v2_trace_hits(
        {
            "runtime_snapshot": {
                "generation": {
                    "pipeline": "V2",
                    "answer": {"used_document_ids": ["document-1"]},
                }
            }
        },
        {"document-1": "故障解决文档5—如何配VAU图像"},
    )

    assert expected["response_types"] == ["ANSWERED"]
    assert "require_reference" not in expected
    assert is_v2 is True
    assert hits == {"故障解决文档5—如何配VAU图像"}
    assert problems == []


def test_v2_trace_expectations_cover_shortcut_and_conversation_routes() -> None:
    runner = _runner()

    assert (
        runner.v2_trace_expectation_problems(
            {"runtime_snapshot": {"generation": {"pipeline": "V2", "input_guard": "GREETING"}}},
            {"trace_input_guard": "GREETING"},
        )
        == []
    )
    assert (
        runner.v2_trace_expectation_problems(
            {
                "runtime_snapshot": {
                    "generation": {
                        "pipeline": "V2",
                        "response_path": "CONVERSATION",
                        "retrieval": {"status": "SKIPPED"},
                    }
                }
            },
            {
                "trace_response_path": "CONVERSATION",
                "trace_retrieval_status": "SKIPPED",
            },
        )
        == []
    )


@pytest.mark.parametrize(
    "answer",
    [
        "当前文档未涉及U盘升级PDC失败后的具体故障处理步骤。",
        "文档未描述升级失败时的排查流程。",
        "当前知识库没有足够的可靠信息，无法确认该问题。",
    ],
)
def test_safe_refusal_expectations_accept_supported_wording_without_references(
    answer: str,
) -> None:
    runner = _runner()
    passed, problems = runner.evaluate({"tolerance": "NONE"}, set())
    answer_problems = runner.evaluate_answer(
        {
            "answer_must_any": [
                "当前文档未涉及",
                "未描述升级失败时的排查流程",
                "无法确认",
                "没有足够",
            ]
        },
        answer,
    )

    assert passed is True
    assert problems == []
    assert answer_problems == []


def test_safe_refusal_tolerance_rejects_exposed_document_reference() -> None:
    runner = _runner()
    passed, problems = runner.evaluate(
        {"tolerance": "NONE"},
        {"故障解决文档14—FFC 更换外壳"},
    )

    assert passed is False
    assert problems == ["范围外问题命中了文档: ['故障解决文档14—FFC 更换外壳']"]


@pytest.mark.parametrize(
    "hits,body,disclaimer,passed",
    [
        (set(), "通用建议：确认连接", None, True),
        ({"如何U盘升级PDC"}, "文档要求FAT32，通用建议更换U盘", "引用仅支持文档事实", True),
        ({"如何U盘升级PDC"}, "通用建议更换U盘", "引用仅支持文档事实", False),
        ({"如何U盘升级PDC"}, "文档要求FAT32", None, False),
        ({"FFC更换外壳"}, "文档要求FAT32", "说明", False),
    ],
)
def test_mixed_answer_expectation_allows_only_grounded_in_scope_references(
    hits, body, disclaimer, passed
):
    runner = _runner()
    expected = {
        "allowed_document_keys": ["U盘升级PDC"],
        "reference_content_must_groups": [["FAT32", "update"]],
        "reference_requires_disclaimer": True,
    }
    document_ok, _ = runner.evaluate(expected, hits)
    content_errors = runner.evaluate_content(
        expected,
        {
            "answer": body,
            "references": [{"title": title} for title in hits],
            "disclaimer": disclaimer,
        },
    )
    assert (document_ok and not content_errors) is passed


def test_no_reference_check_distinguishes_negative_title_mention_from_evidence():
    runner = _runner()
    response = {"answer": "《FFC更换外壳》未涉及压力释放器的故障处理。", "references": []}
    assert runner.extract_hits(response, {}, include_mentions=False) == set()
    response["references"] = [{"title": "FFC更换外壳"}]
    assert runner.extract_hits(response, {}, include_mentions=False) == {"FFC更换外壳"}


def test_document_selection_can_explicitly_accept_public_suggestion_choices():
    runner = _runner()
    response = {
        "answer": "请确认具体是PDC还是FFC，以便定位升级文档。",
        "references": [],
        "next_suggestions": ["如何U盘升级PDC？", "如何升级FFC？"],
    }
    assert runner.extract_hits(response, {}) == set()
    assert runner.extract_hits(response, {}, include_suggestions=True) == {
        "如何U盘升级PDC？",
        "如何升级FFC？",
    }


@pytest.mark.parametrize(
    "answer,passed",
    [
        ('当前知识库没有与"压力释放器"直接相关的文档。请确认具体部件。', True),
        ("当前知识库没有与灯泡直接相关的文档。", False),
    ],
)
def test_missing_document_wording_preserves_the_requested_object(answer, passed):
    runner = _runner()
    path = Path(__file__).resolve().parents[2] / "rag-acceptance" / "cases.jsonl"
    cases = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    expected = next(case for case in cases if case["id"] == "rag_retrieval_001")["expected"]
    assert (not runner.evaluate_answer(expected, answer)) is passed


def test_all_positive_golden_turns_reject_empty_body_even_with_correct_reference():
    import json

    runner = _runner()
    cases = [
        json.loads(line)
        for line in (Path(__file__).resolve().parents[2] / "rag-acceptance/cases.jsonl")
        .read_text()
        .splitlines()
    ]
    checked = 0
    for case in cases:
        for turn in case.get("turns") or [case]:
            keys = turn["expected"].get("document_keys") or []
            if not keys:
                continue
            response = {
                "response_type": "ANSWERED",
                "answer": "",
                "content": [],
                "references": [{"title": keys[0]}],
            }
            assert runner.evaluate_content(turn["expected"], response)
            checked += 1
    assert checked == 47
