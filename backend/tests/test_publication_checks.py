from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.assistant.configuration import AssistantConfiguration
from app.assistant.readiness import ReadinessBlockers, publication_checks
from app.gateway.models import ExecutionPolicy
from app.gateway.repository import GatewayRepository


def test_checks_keep_unknown_blockers_and_do_not_parse_messages() -> None:
    blockers = ReadinessBlockers()
    blockers.add("INDEX", "任意可变文案")
    checks = publication_checks([*blockers, "新条件"], blockers.groups)
    by_code = {item["code"]: item for item in checks}
    assert by_code["INDEX"]["status"] == "BLOCKED"
    assert by_code["INDEX"]["action"]["view"] == "indexes"
    assert by_code["MODELS"]["status"] == "PASSED"
    assert by_code["OTHER"]["messages"] == ["新条件"]


async def test_management_checks_share_actual_runtime_gate(settings: Any) -> None:
    repo = GatewayRepository(AsyncMock(), settings)
    repo.resolve_policy = AsyncMock(return_value=SimpleNamespace(policy=ExecutionPolicy.RAG_ONLY))
    repo.get_readiness = AsyncMock(
        return_value={
            "rag": {"blockers": ["索引错误"], "blocker_groups": {"INDEX": ["索引错误"]}},
            "support": {
                "blockers": ["不相关的历史条件"],
                "blocker_groups": {"POLICY": ["不相关的历史条件"]},
            },
        }
    )
    repo._assistant_rag_understanding_blockers = AsyncMock(return_value=["确认未完成"])
    groups: dict[str, list[str]] = {}
    blockers = await repo.assistant_runtime_blockers(uuid4(), check_details=groups)
    assert blockers == ["索引错误", "确认未完成"]
    assert groups == {"INDEX": ["索引错误"], "UNDERSTANDING": ["确认未完成"]}
    checks = publication_checks(blockers, groups)
    assert {item["code"] for item in checks if item["status"] == "BLOCKED"} == {
        "INDEX",
        "UNDERSTANDING",
    }


async def test_admin_configuration_adds_checks_without_changing_public_contract() -> None:
    gateway = MagicMock()
    gateway.engine.connect.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
    gateway.engine.connect.return_value.__aexit__ = AsyncMock(return_value=None)

    async def blockers(*args: Any, **kwargs: Any) -> list[str]:
        groups = kwargs.get("check_details")
        if groups is not None:
            groups["INDEX"] = ["索引未就绪"]
        return ["索引未就绪"]

    gateway.assistant_runtime_blockers = AsyncMock(side_effect=blockers)
    configuration = AssistantConfiguration(gateway)
    configuration._read = AsyncMock(
        return_value={
            "knowledge_id": uuid4(),
            "status": "ENABLED",
            "assistant_online": True,
            "suggestions": [],
            "revision": 1,
        }
    )
    admin = await configuration.get(uuid4())
    assert admin["ready"] is False
    assert next(item for item in admin["checks"] if item["code"] == "INDEX")["status"] == "BLOCKED"
    public = await configuration.get(uuid4(), public=True)
    assert "checks" not in public
    assert public["ready"] is False
    assert public["blockers"] == admin["blockers"]
    assert gateway.assistant_runtime_blockers.call_args.kwargs["check_details"] is None
