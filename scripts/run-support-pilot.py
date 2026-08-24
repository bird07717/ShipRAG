#!/usr/bin/env python3
"""Import the five-document VDR600 pilot and request one active index build."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import Settings  # noqa: E402

PILOT_DOCUMENTS = (
    "故障解决文档11—如何U盘升级PDC.docx",
    "故障解决文档10—如何更换故障PDC .docx",
    "故障解决文档41-time battery low报警.docx",
    "故障解决文档34—如何手动自检VDR.docx",
    "VDR600如何修改船名.docx",
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path("/home/bird/Documents/VDR600"))
    parser.add_argument("--api", default="http://127.0.0.1:18000/api/v1")
    parser.add_argument("--knowledge-base", default="VDR600 Support Pilot")
    parser.add_argument("--all", action="store_true", help="导入源目录中的全部 DOCX")
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    selected = (
        tuple(path.name for path in sorted(args.source.glob("*.docx")))
        if args.all
        else PILOT_DOCUMENTS
    )
    missing = [name for name in selected if not (args.source / name).is_file()]
    if missing:
        raise SystemExit(f"缺少试点文档：{', '.join(missing)}")
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
            response = client.post(
                f"{args.api}/knowledge-bases",
                headers=headers,
                json={
                    "name": args.knowledge_base,
                    "description": "5篇真实文档精简链路验证",
                },
            )
            response.raise_for_status()
            knowledge = response.json()
            print("KB_CREATED", knowledge["id"])
        else:
            print("KB_REUSED", knowledge["id"])

        knowledge_id = knowledge["id"]
        response = client.get(
            f"{args.api}/knowledge-bases/{knowledge_id}/documents", headers=headers
        )
        response.raise_for_status()
        uploaded = {item["display_name"] for item in response.json()}
        for name in selected:
            display_name = Path(name).stem.strip()
            if display_name in uploaded:
                print("DOC_REUSED", name)
                continue
            path = args.source / name
            with path.open("rb") as stream:
                response = client.post(
                    f"{args.api}/knowledge-bases/{knowledge_id}/documents",
                    headers={**headers, "Idempotency-Key": str(uuid4())},
                    data={"request_build": "false"},
                    files={
                        "file": (
                            name,
                            stream,
                            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        )
                    },
                )
            response.raise_for_status()
            print("DOC_UPLOADED", name)

        response = client.post(
            f"{args.api}/knowledge-bases/{knowledge_id}/indexes/build",
            headers={**headers, "Idempotency-Key": str(uuid4())},
            json={"reason": "MANUAL", "activate_on_success": True},
        )
        response.raise_for_status()
        build = response.json()
        print("BUILD_REQUESTED", build.get("index_id"), build.get("task_id"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
