"""Exercise existing Assistant APIs as a business backend over real HTTP.

Creates conversations under a unique test user; does not change knowledge/indexes.
Uses GOLDEN_ASSISTANT_TOKEN / ASSISTANT_SERVICE_TOKEN (see run_golden.py).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import traceback
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
from run_golden import load_token

PUBLIC = "/api/v1/assistant"
CONTROL = re.compile(
    r"\[(?:MODE:[A-Z_]+|IMG:S\d+|S\d+)\]|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]"
)


async def events(response):
    """Parse SSE independently of TCP chunk and CRLF boundaries."""
    event, data = "message", []
    async for line in response.aiter_lines():
        if not line:
            if data:
                yield event, json.loads("\n".join(data))
            event, data = "message", []
        elif line.startswith("event:"):
            event = line[6:].lstrip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    if data:
        raise AssertionError("SSE ended with an incomplete frame")


def clean_public(value):
    if isinstance(value, dict):
        assert not ({"url", "download_url"} & value.keys()), (
            "internal asset URL exposed"
        )
        for item in value.values():
            clean_public(item)
    elif isinstance(value, list):
        for item in value:
            clean_public(item)


class Check:
    def __init__(self, client, knowledge_id):
        self.client, self.kb = client, knowledge_id
        self.checks = []
        self.conversations = []

    def passed(self, name):
        self.checks.append(name)
        print(f"PASS {name}", flush=True)

    async def error(self, method, path, status, code=None, **kwargs):
        response = await self.client.request(method, path, **kwargs)
        assert response.status_code == status, (path, response.status_code)
        assert "application/json" in response.headers["content-type"]
        if code:
            assert response.json()["error"]["code"] == code, response.json()
        return response

    async def stream(self, body, key, on_accepted=None):
        async with self.client.stream(
            "POST", PUBLIC + "/chat/stream", json=body, headers={"Idempotency-Key": key}
        ) as response:
            assert response.status_code == 200, response.status_code
            assert "text/event-stream" in response.headers["content-type"]
            assert "no-cache" in response.headers["cache-control"]
            assert response.headers["x-accel-buffering"] == "no"
            result = []
            async for event, payload in events(response):
                clean_public(payload)
                result.append((event, payload))
                if event == "accepted":
                    assert len(result) == 1
                    assert payload["request_id"] == response.headers["x-request-id"]
                    if on_accepted:
                        await on_accepted()
                if event == "delta":
                    assert not CONTROL.search(payload["text"]), (
                        "model controls in delta"
                    )
            assert result and result[0][0] == "accepted"
            assert [e for e, _ in result if e in {"complete", "error"}] == [
                result[-1][0]
            ]
            assert result[-1][0] in {"complete", "error"}, "missing terminal event"
            return result

    async def history(self, complete):
        conv = complete["conversation_id"]
        response = await self.client.get(PUBLIC + f"/conversations/{conv}/messages")
        response.raise_for_status()
        messages = response.json()["items"]
        saved = next(m for m in messages if m["message_id"] == complete["message_id"])
        assert saved["status"] == "COMPLETED"
        for stored, live in [("content", "answer"), ("content_blocks", "content")]:
            assert saved[stored] == complete[live], stored
        for field in (
            "response_type",
            "trace_id",
            "references",
            "sections",
            "disclaimer",
            "document",
        ):
            assert saved.get(field) == complete.get(field), field
        return messages

    async def run(self, question, follow):
        assert (await self.client.get("/health/ready")).status_code == 200
        response = await self.client.get(PUBLIC + "/knowledge-bases")
        response.raise_for_status()
        assert any(k["knowledge_id"] == self.kb for k in response.json()["items"])
        welcome = await self.client.get(PUBLIC + f"/knowledge-bases/{self.kb}/welcome")
        welcome.raise_for_status()
        assert len(welcome.json()["suggestions"]) == 3
        self.passed("readiness, published knowledge list and three welcome suggestions")

        for headers, status in [
            ({"Authorization": "Bearer invalid"}, 401),
            ({"Authorization": ""}, 401),
        ]:
            await self.error(
                "GET", PUBLIC + "/knowledge-bases", status, headers=headers
            )
        headers = dict(self.client.headers)
        headers.pop("x-external-user-id")
        async with httpx.AsyncClient(
            base_url=self.client.base_url, headers=headers
        ) as anonymous:
            assert (await anonymous.get(PUBLIC + "/knowledge-bases")).status_code == 422
        await self.error("GET", "/api/v1/knowledge-bases", 401)
        body = {"knowledge_id": self.kb, "question": question, "include_images": True}
        await self.error(
            "POST",
            PUBLIC + "/chat/stream",
            422,
            json={**body, "engine": "rag"},
            headers={"Idempotency-Key": str(uuid4())},
        )
        await self.error("POST", PUBLIC + "/chat/stream", 422, json=body)
        self.passed(
            "credential separation, required identity/key and strict request fields"
        )

        key = str(uuid4())
        first = await self.stream(body, key)
        assert first[-1][0] == "complete", first[-1]
        complete = first[-1][1]
        conv = complete["conversation_id"]
        self.conversations.append(conv)
        assert complete["response_type"] == "DOC_DELIVERED", complete["response_type"]
        assert re.fullmatch(r"\*\*[^\n]+\*\*", complete["answer"].strip())
        context = next(p for e, p in first if e == "context")
        assert context["conversation_id"] == conv
        assert context["assistant_message_id"] == complete["message_id"]
        assert context["trace_id"] == complete["trace_id"]
        assert complete["content"] and complete["references"]
        self.passed("SSE ordering/headers/IDs and full document presentation")

        images = [
            b["image_asset_id"] for b in complete["content"] if b["type"] == "image"
        ]
        assert images, "choose a document with images for this contract check"
        image_path = (
            PUBLIC + f"/knowledge-bases/{self.kb}/image-assets/{images[0]}/content"
        )
        asset = await self.client.get(image_path)
        assert asset.status_code == 200 and asset.content
        assert asset.headers["content-type"].startswith("image/")
        assert asset.headers["x-content-type-options"] == "nosniff"
        await self.error(
            "GET",
            PUBLIC + f"/knowledge-bases/{uuid4()}/image-assets/{images[0]}/content",
            404,
        )
        await self.error(
            "GET",
            PUBLIC + f"/knowledge-bases/{self.kb}/image-assets/{uuid4()}/content",
            404,
        )
        self.passed("authenticated image bytes and knowledge/asset isolation")

        messages = await self.history(complete)
        page = await self.client.get(
            PUBLIC + f"/conversations/{conv}/messages", params={"limit": 1}
        )
        tail = page.json()
        older = await self.client.get(
            PUBLIC + f"/conversations/{conv}/messages",
            params={"limit": 1, "before": tail["next_before"]},
        )
        assert [m["message_id"] for m in older.json()["items"] + tail["items"]] == [
            m["message_id"] for m in messages
        ]
        assert older.json()["next_before"] is None
        listing = await self.client.get(
            PUBLIC + "/conversations", params={"knowledge_id": self.kb}
        )
        assert conv in [c["conversation_id"] for c in listing.json()["items"]]
        assert (
            await self.client.get(PUBLIC + f"/conversations/{conv}")
        ).status_code == 200
        replay = await self.stream(body, key)
        assert [e for e, _ in replay] == ["accepted", "complete"]
        assert replay[-1][1] == complete
        await self.error(
            "POST",
            PUBLIC + "/chat/stream",
            409,
            "IDEMPOTENCY_KEY_REUSED",
            json={**body, "question": question + "？"},
            headers={"Idempotency-Key": key},
        )
        self.passed(
            "live/history equality, pagination, conversation list and exact replay"
        )

        other = {"X-External-User-ID": "other-" + str(uuid4())}
        for path in (f"/conversations/{conv}", f"/conversations/{conv}/messages"):
            await self.error("GET", PUBLIC + path, 404, headers=other)
        await self.error(
            "POST",
            PUBLIC + "/chat/stream",
            404,
            json={**body, "conversation_id": conv},
            headers={**other, "Idempotency-Key": str(uuid4())},
        )
        self.passed("cross-user conversation/history/continuation isolation")

        follow_body = {
            **body,
            "conversation_id": conv,
            "question": follow,
            "include_images": False,
        }
        follow_key = str(uuid4())

        async def competing_requests():
            await self.error(
                "POST",
                PUBLIC + "/chat/stream",
                409,
                "REQUEST_IN_PROGRESS",
                json=follow_body,
                headers={"Idempotency-Key": follow_key},
            )
            await self.error(
                "POST",
                PUBLIC + "/chat/stream",
                409,
                "CONVERSATION_BUSY",
                json=follow_body,
                headers={"Idempotency-Key": str(uuid4())},
            )

        result = await self.stream(follow_body, follow_key, competing_requests)
        assert result[-1][0] == "complete", result[-1]
        answer = result[-1][1]
        assert answer["response_type"] == "ANSWERED", answer["response_type"]
        assert not CONTROL.search(answer["answer"])
        assert all(b["type"] != "image" for b in answer["content"])
        assert isinstance(answer["references"], list)
        if answer.get("disclaimer") is not None:
            assert (
                isinstance(answer["disclaimer"], str) and answer["disclaimer"].strip()
            )
        await self.history(answer)
        assert (await self.stream(follow_body, follow_key))[-1][1] == answer
        self.passed(
            "explanation, no-image response, optional disclaimer consistency and concurrency"
        )

        cancel_body = {
            **follow_body,
            "question": follow + "请详细解释其中的原因和适用条件。",
        }
        cancel_key = str(uuid4())
        async with self.client.stream(
            "POST",
            PUBLIC + "/chat/stream",
            json=cancel_body,
            headers={"Idempotency-Key": cancel_key},
        ) as response:
            assert response.status_code == 200
            async for event, payload in events(response):
                if event == "context":
                    cancelled_message = payload["assistant_message_id"]
                    break
            else:
                raise AssertionError("no context before cancellation")
        for _ in range(40):
            response = await self.client.get(PUBLIC + f"/conversations/{conv}/messages")
            saved = next(
                m
                for m in response.json()["items"]
                if m["message_id"] == cancelled_message
            )
            if saved["status"] != "GENERATING":
                break
            await asyncio.sleep(0.1)
        assert saved["status"] == "CANCELLED", saved["status"]
        assert saved["error"]["retryable"] is True
        replay = await self.stream(cancel_body, cancel_key)
        assert [e for e, _ in replay] == ["accepted", "error"]
        assert replay[-1][1]["code"] == "CLIENT_DISCONNECTED"
        assert replay[-1][1]["partial_content"] == saved["content"]
        resumed = await self.stream(
            {**follow_body, "question": "换一个问题"}, str(uuid4())
        )
        assert resumed[-1][0] == "complete", resumed[-1]
        await self.history(resumed[-1][1])
        self.passed(
            "real HTTP disconnect releases conversation and replays cancellation"
        )


async def main(args):
    user = "contract-check-" + str(uuid4())
    headers = {
        "Authorization": "Bearer " + load_token("assistant"),
        "X-External-User-ID": user,
    }
    async with httpx.AsyncClient(
        base_url=args.base_url, headers=headers, timeout=90
    ) as client:
        check = Check(client, args.kb_id)
        error = None
        try:
            await check.run(args.question, args.follow)
        except Exception as exc:
            frame = traceback.extract_tb(exc.__traceback__)[-1]
            error = f"{type(exc).__name__}: {exc} ({Path(frame.filename).name}:{frame.lineno})"
        report = {
            "passed": error is None,
            "checks": check.checks,
            "error": error,
            "external_user_id": user,
            "conversation_ids": check.conversations,
        }
        target = Path(args.report)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Report: {target}")
        if error:
            raise SystemExit(error)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--kb-id", required=True)
    parser.add_argument("--question", default="如何更换PSU电源板")
    parser.add_argument("--follow", default="为什么要给线缆拍照并做标记？")
    parser.add_argument(
        "--report",
        default="rag-acceptance/reports/assistant-contract-"
        + datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        + ".json",
    )
    asyncio.run(main(parser.parse_args()))
