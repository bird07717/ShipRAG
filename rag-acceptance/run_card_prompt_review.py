"""REAL LLM comparison of the document-card prompt before and after a change.

Why: the thinking-card experiment found that existing CONFIRMED cards lose the trigger conditions of
conditional steps (a card listed "dry the box / temporary docking / replace" as plain tasks without
"if there is water / if the corrosion is severe"), list tools as "prerequisites" (the field is not
defined in the prompt), and expand an abbreviation the source never expands. Card tasks feed the
document-level Rerank input, so a card that drops conditions can mis-rank.

Arms: OLD = ``backend/app/rag/document_cards.py`` at a git ref (default HEAD),
NEW = the working tree. Both run the production ``DocumentCardGenerator`` on the same documents.
Generation only: nothing is written to the database, no card is replaced, no review status changes.

Documents: the 13 whose existing cards were examined in ``reports/thinking-card-20261009``.
Repeats: ``--repeats`` (default 2) per document and arm, all results kept.

Protocol fixed BEFORE the first paid call (do not edit after seeing results):
  * Gate 1, validity: card generation errors (after the generator's own retries) in NEW exceed
    OLD's by at most 1 over the 26 samples.
  * Gate 2, conditions kept: among tasks that name a conditional action (吹干, 对接, 更换, 申请),
    the share that also carries a condition word (如, 若, 有, 当, 时, 腐蚀, 水渍, 严重, 情况,
    出现, 发现) is higher in NEW than in OLD (mechanical screen), AND by reading the cards of the
    documents flagged earlier (the PDC check and replacement documents, the LRU check document, the
    MU board check document) NEW states the source's trigger conditions where OLD dropped them.
  * Gate 3, prerequisites: items naming tools, spares or preparation (工具, 备件, 万用表, 螺丝刀,
    准备, 吹风, 仪器) are fewer in NEW than in OLD.
  * Gate 4, no invented expansion: the phrase 电源分配 appears in a NEW card only if it appears in
    the source document.
  * Reported, not gated: document_type changes, summary length, list sizes.
  Adopt the prompt change only if all four gates hold. A failing gate is reported as it is and the
  change is not adopted; the prompt text is not tuned against these documents afterwards without
  recording that as a second iteration.
No model judge. The mechanical screens are aids; gate 2's second half and gate 3 are confirmed by
reading.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.rag.document_card_repository import DocumentCardRepository
from app.rag.llm import FakeLlmProvider, create_llm_provider
from app.rag.models import ModelSnapshot
from app.rag.repository import RagRepository

ARMS = ("OLD", "NEW")
CARD_MODULE = "backend/app/rag/document_cards.py"
ACTION = re.compile(r"吹干|对接|更换|申请")
CONDITION = re.compile(r"如|若|有|当|时|腐蚀|水渍|严重|情况|出现|发现")
TOOLISH = re.compile(r"工具|备件|万用表|螺丝刀|准备|吹风|仪器")
INVENTED = "电源分配"


def load_old_module(ref: str) -> ModuleType:
    source = subprocess.run(
        ["git", "show", f"{ref}:{CARD_MODULE}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    handle = tempfile.NamedTemporaryFile(
        "w", suffix="_document_cards_old.py", delete=False
    )
    handle.write(source)
    handle.close()
    spec = importlib.util.spec_from_file_location("document_cards_old", handle.name)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["document_cards_old"] = module
    spec.loader.exec_module(module)
    return module


def summarize(path: Path) -> None:
    report = json.loads(path.read_text())
    stats: dict[str, dict[str, Any]] = {
        arm: {
            "ok": 0,
            "err": 0,
            "act": 0,
            "act_cond": 0,
            "tool": 0,
            "invented": 0,
            "type": [],
        }
        for arm in ARMS
    }
    for row in report["results"]:
        for run in row["runs"]:
            for arm in ARMS:
                v = run["arms"].get(arm)
                if v is None:
                    continue
                if "error" in v:
                    stats[arm]["err"] += 1
                    continue
                card = v["card"]
                stats[arm]["ok"] += 1
                stats[arm]["type"].append(card["document_type"])
                for task in card["tasks"]:
                    if ACTION.search(task):
                        stats[arm]["act"] += 1
                        stats[arm]["act_cond"] += bool(CONDITION.search(task))
                stats[arm]["tool"] += sum(
                    bool(TOOLISH.search(i)) for i in card["prerequisites"]
                )
                blob = json.dumps(card, ensure_ascii=False)
                if INVENTED in blob and INVENTED not in row["source_text"]:
                    stats[arm]["invented"] += 1
    print("== GATE 1 validity (generation errors after the generator's own retries)")
    for arm in ARMS:
        print(f"  {arm}: ok={stats[arm]['ok']} errors={stats[arm]['err']}")
    print(
        "== GATE 2 mechanical: tasks naming an action that also carry a condition word"
    )
    for arm in ARMS:
        a = stats[arm]
        share = f"{a['act_cond']}/{a['act']}" + (
            f" = {a['act_cond'] / a['act']:.0%}" if a["act"] else ""
        )
        print(f"  {arm}: {share}")
    print("== GATE 3 prerequisites that name tools/spares/preparation")
    for arm in ARMS:
        print(f"  {arm}: {stats[arm]['tool']}")
    print(f"== GATE 4 cards containing '{INVENTED}' that the source never mentions")
    for arm in ARMS:
        print(f"  {arm}: {stats[arm]['invented']}")
    changed = sum(
        1
        for row in report["results"]
        for run in row["runs"]
        if all(a in run["arms"] and "card" in run["arms"][a] for a in ARMS)
        and run["arms"]["OLD"]["card"]["document_type"]
        != run["arms"]["NEW"]["card"]["document_type"]
    )
    print(
        f"== reported: document_type differs between OLD and NEW in {changed} paired samples"
    )


def show(path: Path, needle: str) -> None:
    report = json.loads(path.read_text())
    for row in report["results"]:
        if needle not in row["display_name"]:
            continue
        print(f"##### {row['display_name']}")
        for run in row["runs"]:
            for arm in ARMS:
                v = run["arms"].get(arm, {})
                if "card" not in v:
                    print(f"[{arm} #{run['repeat']}] ERROR {v.get('code')}")
                    continue
                c = v["card"]
                print(f"[{arm} #{run['repeat']}] type={c['document_type']}")
                print("   summary:", c["summary"])
                for task in c["tasks"]:
                    print("   task   :", task)
                for item in c["prerequisites"]:
                    print("   prereq :", item)
                for item in c["exclusions"]:
                    print("   exclude:", item)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--summarize", type=Path)
    parser.add_argument(
        "--show", help="with --summarize: print cards whose name contains this"
    )
    parser.add_argument(
        "--ref", default="HEAD", help="git ref holding the OLD card module"
    )
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument(
        "--cards",
        type=Path,
        default=Path("rag-acceptance/reports/thinking-card-20261009/answers.json"),
    )
    args = parser.parse_args()
    if args.summarize:
        summarize(args.summarize)
        if args.show:
            show(args.summarize, args.show)
        return
    if args.output is None:
        parser.error("--output is required")
    if args.output.exists():
        parser.error(
            "Output already exists; preserve previous runs with a new filename"
        )

    cards = json.loads(args.cards.read_text())
    doc_ids = [snap["document_id"] for snap in cards["card_snapshots"]]
    kb_id, index_id = UUID(cards["knowledge_id"]), UUID(cards["index_id"])
    old = load_old_module(args.ref)
    from app.rag import document_cards as new

    settings = Settings()
    engine = create_async_engine(settings.database_url.get_secret_value())
    repo = RagRepository(engine, settings)
    card_repo = DocumentCardRepository(engine, settings)
    report: dict[str, Any] = {
        "started_at": datetime.now(UTC).isoformat(),
        "method": "document-card prompt OLD vs NEW; generation only, no database writes",
        "old_ref": args.ref,
        "prompt_versions": {
            "OLD": old.DOCUMENT_CARD_PROMPT_VERSION,
            "NEW": new.DOCUMENT_CARD_PROMPT_VERSION,
        },
        "prompt_sha256": {
            "OLD": __import__("hashlib")
            .sha256(old.build_document_card_system_prompt().encode())
            .hexdigest(),
            "NEW": __import__("hashlib")
            .sha256(new.build_document_card_system_prompt().encode())
            .hexdigest(),
        },
        "repeats": args.repeats,
        "knowledge_id": str(kb_id),
        "index_id": str(index_id),
        "results": [],
    }

    def save() -> None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    try:
        async with engine.connect() as db:
            model = dict(
                (
                    await db.execute(
                        text(
                            "SELECT * FROM model_config WHERE model_type='LLM' AND enabled"
                        )
                    )
                )
                .mappings()
                .one()
            )
        snapshot = ModelSnapshot(
            provider=model["provider"],
            model_name=model["model_name"],
            parameters=model["parameters"] or {},
            base_url=model["base_url"],
            api_key=repo._decrypt_api_key(model),
        )
        check = create_llm_provider(settings, snapshot)
        try:
            if isinstance(check, FakeLlmProvider) or snapshot.provider != "dashscope":
                raise ValueError("Require the real DashScope model")
        finally:
            await check.aclose()
        report["model"] = {"provider": snapshot.provider, "name": snapshot.model_name}

        documents = {
            str(d["document_id"]): d
            for d in await card_repo.generation_documents(kb_id, index_id)
        }
        rows: list[dict[str, Any]] = []
        for did in doc_ids:
            doc = documents[did]
            rows.append(
                {
                    "document_id": did,
                    "display_name": str(
                        doc.get("display_name") or doc.get("filename") or ""
                    ),
                    "source_text": str(doc.get("content") or ""),
                    "runs": [],
                    "_doc": doc,
                }
            )
        report["results"] = [{k: v for k, v in r.items() if k != "_doc"} for r in rows]
        save()
        if args.prepare_only:
            print(
                f"Prepared {len(rows)} documents x {len(ARMS)} arms x {args.repeats}; no paid calls"
            )
            print("prompt versions:", report["prompt_versions"])
            return

        semaphore = asyncio.Semaphore(args.concurrency)

        async def capture(index: int, repeat: int) -> None:
            row, public = rows[index], report["results"][index]
            order = list(ARMS) if (index + repeat) % 2 else list(reversed(ARMS))
            run: dict[str, Any] = {"repeat": repeat, "order": order, "arms": {}}
            public["runs"].append(run)
            module = {"OLD": old, "NEW": new}
            async with semaphore:
                for arm in order:
                    provider = create_llm_provider(settings, snapshot)
                    started = time.perf_counter()
                    try:
                        generator = module[arm].DocumentCardGenerator(settings)
                        payload, meta = await generator.generate(row["_doc"], provider)
                        result: dict[str, Any] = {
                            "card": payload.model_dump(mode="json"),
                            "prompt_version": meta["prompt_version"],
                            "usage": dict(provider.usage),
                        }
                    except Exception as exc:
                        result = {
                            "error": type(exc).__name__,
                            "code": getattr(exc, "code", None),
                            "detail": str(getattr(exc, "detail", ""))[:300],
                        }
                    finally:
                        await provider.aclose()
                    result["latency_ms"] = round((time.perf_counter() - started) * 1000)
                    run["arms"][arm] = result
                    save()
                    status = result.get("code") if "error" in result else "ok"
                    print(
                        f"{row['display_name'][:30]} #{repeat} {arm}: {status}",
                        flush=True,
                    )

        await asyncio.gather(
            *[
                capture(i, r)
                for i in range(len(rows))
                for r in range(1, args.repeats + 1)
            ]
        )
        report["completed_at"] = datetime.now(UTC).isoformat()
        save()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
