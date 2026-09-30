"""Deterministic checks of saved public answers; no model-based judging."""

from __future__ import annotations

import re
import unicodedata
from typing import Any


def normalized(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).casefold()


def response_text(response: dict[str, Any]) -> str:
    """Use the delivered body, including tables; answer may be only a title."""
    blocks = response.get("content") or []
    texts = [str(b.get("text") or "") for b in blocks if b.get("type") in {"text", "table"}]
    if not blocks:
        texts = [str(response.get("answer") or "")]
    return "\n".join(texts).strip()


def evaluate_content(expected: dict[str, Any], response: dict[str, Any]) -> list[str]:
    body = response_text(response)
    norm = normalized(body)
    problems: list[str] = []
    blocks = response.get("content") or []
    images = [b for b in blocks if b.get("type") == "image" and b.get("image_asset_id")]
    response_type = response.get("response_type")
    if not body and not images:
        problems.append("回答正文为空")
    if response_type in {"DOC_DELIVERED", "DOCUMENT_DELIVERED"}:
        titles = {normalized(str(r.get("title") or "")) for r in response.get("references") or []}
        plain = normalized(re.sub(r"[《》*\\]", "", body))
        if (not plain or plain in titles) and not images:
            problems.append("文档交付只有标题，缺少正文")
    allowed = expected.get("response_types")
    if allowed and response_type not in allowed:
        problems.append(f"响应类型不符: {response_type}，期望 {allowed}")
    allowed_modes = expected.get("answer_modes")
    if allowed_modes and response.get("answer_mode") not in allowed_modes:
        problems.append(f"回答模式不符: {response.get('answer_mode')}，期望 {allowed_modes}")
    for key in expected.get("content_must_all") or []:
        if normalized(str(key)) not in norm:
            problems.append(f"正文缺少必需内容: {key}")
    for alternatives in expected.get("content_must_groups") or []:
        if not any(normalized(str(key)) in norm for key in alternatives):
            problems.append(f"正文缺少必需内容组: {alternatives}")
    for key in expected.get("content_forbidden_any") or []:
        if normalized(str(key)) in norm:
            problems.append(f"正文包含禁止内容: {key}")
    # Patterns run on the whitespace-free, casefolded body so wording spacing never matters.
    for pattern in expected.get("content_forbidden_patterns") or []:
        if match := re.search(str(pattern), norm):
            problems.append(f"正文匹配禁止模式: {pattern} -> {match.group(0)}")
    if len(images) < int(expected.get("min_images", 0)):
        problems.append("回答缺少必需图片")
    tables = [b for b in blocks if b.get("type") == "table" and str(b.get("text") or "").strip()]
    if len(tables) < int(expected.get("min_tables", 0)):
        problems.append("回答缺少必需表格")
    expected_ids = set(expected.get("document_ids") or [])
    references = response.get("references") or []
    if expected.get("require_no_reference") and references:
        problems.append("回答不应包含文档引用")
    disclaimer = str(response.get("disclaimer") or "")
    if expected.get("require_disclaimer") and not disclaimer:
        problems.append("回答缺少免责声明")
    disclaimer_norm = normalized(disclaimer)
    for key in expected.get("disclaimer_must_all") or []:
        if normalized(str(key)) not in disclaimer_norm:
            problems.append(f"免责声明缺少必需内容: {key}")
    if references:
        for alternatives in expected.get("reference_content_must_groups") or []:
            if not any(normalized(str(key)) in norm for key in alternatives):
                problems.append(f"有引用的回答缺少文档事实: {alternatives}")
        if expected.get("reference_requires_disclaimer") and not response.get("disclaimer"):
            problems.append("混合回答有引用但缺少依据范围说明")
    if (
        expected.get("require_reference")
        or response_type
        in {
            "DOC_DELIVERED",
            "DOCUMENT_DELIVERED",
        }
    ) and not references:
        problems.append("回答缺少文档引用")
    actual_ids = {str(r["document_id"]) for r in references if r.get("document_id")}
    if expected_ids and not expected_ids.issubset(actual_ids):
        problems.append("引用缺少期望文档 ID")
    forbidden_ids = set(expected.get("forbidden_document_ids") or [])
    if forbidden_ids.intersection(actual_ids):
        problems.append("引用包含禁止文档 ID")
    # Internal/admin responses expose sources; public Assistant responses may not.
    source_ids = {
        str(s["document_id"]) for s in response.get("sources") or [] if s.get("document_id")
    }
    if source_ids and actual_ids and not source_ids.issubset(actual_ids):
        problems.append("来源与文档引用不一致")
    return problems
