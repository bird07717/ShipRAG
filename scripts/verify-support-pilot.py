#!/usr/bin/env python3
"""Create alias-free pilot profiles and verify formal Support behavior over HTTP."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import Settings  # noqa: E402

PROFILES = (
    (
        "OPU settings for SVDR",
        "OPU_ALARM_SETTINGS",
        "如何设置 OPU 告警开关？",
        "OPU",
        "CONFIGURATION",
    ),
    (
        "VDR600如何修改船名",
        "VDR_SHIP_NAME_CHANGE",
        "VDR600 如何修改船名？",
        "VDR600",
        "CONFIGURATION",
    ),
    (
        "故障解决文档6—如何检查VAU通讯线缆",
        "VAU_COMMUNICATION_CABLE_CHECK",
        "如何检查 VAU 通讯线缆？",
        "VAU",
        "CHECK",
    ),
    (
        "故障解决文档7—如何DAU通讯线缆",
        "DAU_COMMUNICATION_CABLE_CHECK",
        "如何检查 DAU 通讯线缆？",
        "DAU",
        "CHECK",
    ),
    (
        "故障解决文档8—如何NAU通讯线缆",
        "NAU_COMMUNICATION_CABLE_CHECK",
        "如何检查 NAU 通讯线缆？",
        "NAU",
        "CHECK",
    ),
    (
        "故障解决文档9—PDC 如何检查接线盒",
        "PDC_JUNCTION_BOX_CHECK",
        "PDC 如何检查接线盒？",
        "PDC",
        "CHECK",
    ),
    (
        "故障解决文档10—如何更换故障PDC",
        "PDC_UNIT_REPLACEMENT",
        "如何更换故障 PDC？",
        "PDC",
        "REPLACEMENT",
    ),
    (
        "故障解决文档11—如何U盘升级PDC",
        "PDC_UPDATE_UPGRADE",
        "如何通过 U 盘升级 PDC？",
        "PDC",
        "UPGRADE",
    ),
    (
        "故障解决文档13—FFC 如何检查接线盒",
        "FFC_JUNCTION_BOX_CHECK",
        "FFC 如何检查接线盒？",
        "FFC",
        "CHECK",
    ),
    (
        "故障解决文档14—FFC 更换外壳",
        "FFC_HOUSING_REPLACEMENT",
        "如何更换 FFC 外壳？",
        "FFC",
        "REPLACEMENT",
    ),
    ("故障解决文档15-FFC 如何更换", "FFC_UNIT_REPLACEMENT", "如何更换 FFC？", "FFC", "REPLACEMENT"),
    ("故障解决文档16—如何升级FFC", "FFC_UPDATE_UPGRADE", "如何升级 FFC？", "FFC", "UPGRADE"),
    (
        "故障解决文档17—如何检查LRU通讯线缆",
        "LRU_COMMUNICATION_CABLE_CHECK",
        "如何检查 LRU 通讯线缆？",
        "LRU",
        "CHECK",
    ),
    (
        "故障解决文档18—如何更换LRU单元",
        "LRU_UNIT_REPLACEMENT",
        "如何更换 LRU 单元？",
        "LRU",
        "REPLACEMENT",
    ),
    ("故障解决文档19—如何升级LRU", "LRU_UPDATE_UPGRADE", "如何升级 LRU？", "LRU", "UPGRADE"),
    (
        "故障解决文档22-如何更换蓄电池组",
        "BATTERY_PACK_REPLACEMENT",
        "如何更换蓄电池组？",
        "VDR600",
        "REPLACEMENT",
    ),
    (
        "故障解决文档28-如何更换VDR600 OPU单元",
        "OPU_UNIT_REPLACEMENT",
        "如何更换 VDR600 OPU 单元？",
        "OPU",
        "REPLACEMENT",
    ),
    (
        "故障解决文档29—检查存储体内的数据",
        "STORAGE_DATA_CHECK",
        "如何检查存储体内的数据？",
        "VDR600",
        "CHECK",
    ),
    (
        "故障解决文档33—如何检查VAU线缆",
        "VAU_VIDEO_CABLE_CHECK",
        "如何检查 VAU 视频线缆？",
        "VAU",
        "CHECK",
    ),
    (
        "故障解决文档34—如何手动自检VDR",
        "VDR_MANUAL_SELF_TEST",
        "如何手动自检 VDR？",
        "VDR600",
        "CHECK",
    ),
    ("故障解决文档35—检查麦克风", "MICROPHONE_CHECK", "如何检查麦克风故障？", "VDR600", "CHECK"),
    (
        "故障解决文档38-如何检查update版本并使用正确配置软件配置网络图像",
        "UPDATE_VERSION_NETWORK_IMAGE_CONFIG",
        "如何检查 update 版本并配置网络图像？",
        "VDR600",
        "CONFIGURATION",
    ),
    (
        "故障解决文档40-面板上的UTC时间",
        "UTC_TIME_PANEL",
        "面板 UTC 时间异常如何处理？",
        "VDR600",
        "ALARM",
    ),
    (
        "故障解决文档41-time battery low报警",
        "TIME_BATTERY_LOW",
        "time battery low 报警如何处理？",
        "VDR600",
        "ALARM",
    ),
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:18000/api/v1")
    parser.add_argument("--knowledge-base", default="VDR600 Support Pilot")
    return parser.parse_args()


def _post_chat(
    client: httpx.Client,
    api: str,
    headers: dict[str, str],
    payload: dict[str, object],
) -> dict[str, object]:
    response = client.post(
        f"{api}/support/chat",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        json=payload,
    )
    response.raise_for_status()
    return response.json()


def main() -> int:
    args = _arguments()
    settings = Settings()
    if settings.service_token is None:
        raise SystemExit("SERVICE_TOKEN 未配置")
    headers = {"Authorization": f"Bearer {settings.service_token.get_secret_value()}"}

    with httpx.Client(timeout=180) as client:
        response = client.get(f"{args.api}/knowledge-bases", headers=headers)
        response.raise_for_status()
        knowledge = next(
            (item for item in response.json() if item["name"] == args.knowledge_base), None
        )
        if knowledge is None:
            raise SystemExit("试点知识库不存在，请先运行 run-support-pilot.py")
        knowledge_id = knowledge["id"]
        if knowledge.get("runtime_state") != "READY":
            raise SystemExit(f"试点知识库尚未就绪：{knowledge.get('runtime_state')}")

        response = client.get(
            f"{args.api}/knowledge-bases/{knowledge_id}/documents", headers=headers
        )
        response.raise_for_status()
        documents = {item["display_name"]: item for item in response.json()}
        for filename, code, question, product, task_type in PROFILES:
            document = documents.get(filename)
            if document is None:
                available = "、".join(sorted(documents))
                raise SystemExit(f"缺少已上传文档：{filename}；当前文档：{available}")
            response = client.put(
                f"{args.api}/support/problem-profiles",
                params={"knowledge_id": knowledge_id},
                headers=headers,
                json={
                    "document_id": document["id"],
                    "problem_code": code,
                    "canonical_question": question,
                    "aliases": [],
                    "keywords": [],
                    "product_name": product,
                    "model_names": ["VDR600"],
                    "task_type": task_type,
                    "enabled": True,
                },
            )
            response.raise_for_status()
            profile = response.json()
            if not profile["support_ready"]:
                raise SystemExit(f"Profile 未就绪：{code} {profile['readiness_reasons']}")
            if profile["aliases"]:
                raise SystemExit(f"Alias 应为空：{code}")
            print("PROFILE_READY", code)

        delivered: dict[str, dict[str, object]] = {}
        for _, code, question, _, _ in PROFILES:
            result = _post_chat(
                client,
                args.api,
                headers,
                {
                    "knowledge_id": knowledge_id,
                    "question": question,
                    "request_id": f"pilot-{uuid4()}",
                    "user_id": "pilot-verifier",
                    "include_images": True,
                },
            )
            if result["response_type"] != "DOCUMENT_DELIVERED":
                raise SystemExit(f"首轮未完整交付：{code} {result['response_type']}")
            if result.get("usage"):
                raise SystemExit(f"首轮不应调用回答模型：{code} {result['usage']}")
            content = result.get("content") or []
            if not content:
                raise SystemExit(f"首轮内容为空：{code}")
            sequences = [block.get("sequence_no") for block in content]
            if sequences != sorted(sequences):
                raise SystemExit(f"Element 顺序错误：{code} {sequences}")
            delivered[code] = result
            print(
                "DOCUMENT_DELIVERED",
                code,
                f"elements={len(content)}",
                f"images={len(result.get('images') or [])}",
            )

        catalog_result = _post_chat(
            client,
            args.api,
            headers,
            {
                "knowledge_id": knowledge_id,
                "question": "我想拿U盘给PDC刷一下新版本",
                "request_id": f"pilot-{uuid4()}",
                "user_id": "pilot-verifier",
                "include_images": True,
            },
        )
        routing = catalog_result.get("routing") or {}
        catalog_document = catalog_result.get("document") or {}
        if catalog_result["response_type"] != "DOCUMENT_DELIVERED":
            raise SystemExit(f"无 Alias 目录选择失败：{catalog_result['response_type']}")
        if catalog_document.get("problem_code") != "PDC_UPDATE_UPGRADE":
            raise SystemExit(f"无 Alias 目录选错问题：{catalog_document}")
        print("CATALOG_MATCH", routing.get("method"), catalog_document.get("problem_code"))

        first = delivered["PDC_UPDATE_UPGRADE"]
        follow_up = _post_chat(
            client,
            args.api,
            headers,
            {
                "knowledge_id": knowledge_id,
                "conversation_id": first["conversation_id"],
                "question": "请把文档中的第一个操作步骤解释得更容易理解。",
                "request_id": f"pilot-{uuid4()}",
                "user_id": "pilot-verifier",
                "include_images": True,
            },
        )
        if follow_up["response_type"] not in {"ANSWERED", "NEED_MORE_INFORMATION"}:
            raise SystemExit(f"后续回答异常：{follow_up['response_type']}")
        print(
            "FOLLOW_UP",
            follow_up["response_type"],
            f"sections={len(follow_up.get('sections') or [])}",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
