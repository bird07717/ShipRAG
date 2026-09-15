from __future__ import annotations

import ipaddress
import re
import unicodedata
from typing import Any
from urllib.parse import urlsplit

from app.rag.llm import LlmError
from app.rag.presentation import clean_model_text
from app.rag.response import (
    OUT_OF_SCOPE_MESSAGE,
    TECHNICAL_DISCLAIMER,
    AnswerMode,
    ChatResult,
    ContentBlock,
    ResponseType,
)
from app.rag.v2.answer_generator import GeneratedAnswer
from app.rag.v2.document_context import DocumentContext

_IMAGE_MARKER_PATTERN = re.compile(r"\[IMG:([^\]\r\n]{1,100})\]", re.IGNORECASE)
_IMAGE_KEY_PATTERN = re.compile(r"(?i)(?<![A-Z0-9])I[1-9][0-9]*(?![A-Z0-9])")
_ABSOLUTE_URL_PATTERN = re.compile(r"(?i)https?://[^\s)\]}>]+")
_MANAGEMENT_PATH_PATTERN = re.compile(
    r"(?i)/(?:api/v1/)?(?:admin|management)(?=$|[/\s?#.,;:，。；：！、])"
)
_INTERNAL_RESOURCE_PATTERN = re.compile(r"(?i)\b(?:download_url|minio_bucket|minio_object_key)\b")
_PROMPT_MARKER_PATTERN = re.compile(
    r"(?i)(?:<document\s+key=|hidden_document_context|available_document_keys"
    r"|available_image_keys|used_document_keys|used_image_keys|output_schema)"
)
_IPV4_PATTERN = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
_VERSION_PATTERN = re.compile(
    r"(?i)(?<!\w)(?:v|ver(?:sion)?|版本)\s*[:：]?\s*\d+(?:\.\d+){1,3}(?![\w.])"
)
_MEASUREMENT_PATTERN = re.compile(
    r"(?i)(?<![\w.])(?:AC|DC)?\s*\d+(?:\.\d+)?\s*"
    r"(?:kHz|MHz|GHz|Hz|mV|mA|mW|kW|ms|MB|GB|TB|V|A|W|s|℃|°C)(?!\w)"
)
_PORT_PATTERN = re.compile(r"(?i)(?:端口|port)\s*[:：]?\s*\d{1,5}(?!\d)")


def _normalized_evidence(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).casefold().split())


def _is_internal_host(hostname: str | None) -> bool:
    if not hostname:
        return False
    host = hostname.casefold().rstrip(".")
    if host in {"localhost", "backend", "minio", "postgres", "redis"} or host.endswith(".internal"):
        return True
    return False


def _is_evidenced(value: str, allowed_text: str) -> bool:
    return bool(allowed_text) and _normalized_evidence(value) in _normalized_evidence(allowed_text)


def public_leaks(
    answer: str,
    aliases: tuple[str, ...],
    *,
    allowed_text: str = "",
) -> list[str]:
    """Return actual implementation controls found in model-visible text.

    Document titles and bare D/S/I-style identifiers are valid technical
    content and must not be guessed to be private. ``aliases`` remains in the
    signature for one-stage compatibility but is intentionally ignored.
    """

    del aliases
    visible = _IMAGE_MARKER_PATTERN.sub("", answer)
    leaks: list[str] = []
    if match := _PROMPT_MARKER_PATTERN.search(visible):
        leaks.append(f"prompt_marker:{match.group(0)}")
    if match := _INTERNAL_RESOURCE_PATTERN.search(visible):
        value = match.group(0)
        if not _is_evidenced(value, allowed_text):
            leaks.append(f"internal_resource:{value}")
    absolute_url_spans: list[tuple[int, int]] = []
    for match in _ABSOLUTE_URL_PATTERN.finditer(visible):
        absolute_url_spans.append(match.span())
        value = match.group(0).rstrip(".,;:，。；：")
        if _is_internal_host(urlsplit(value).hostname) and not _is_evidenced(value, allowed_text):
            leaks.append(f"internal_url:{value}")
            break
    match = next(
        (
            candidate
            for candidate in _MANAGEMENT_PATH_PATTERN.finditer(visible)
            if not any(start <= candidate.start() < end for start, end in absolute_url_spans)
        ),
        None,
    )
    if match is not None:
        value = match.group(0).strip()
        if not _is_evidenced(value, allowed_text):
            leaks.append(f"management_path:{value}")
    return leaks


def parameter_observations(
    answer: str,
    *,
    question: str,
    context: DocumentContext,
) -> list[dict[str, Any]]:
    """Record typed parameter provenance without changing or rejecting an answer."""

    values: list[tuple[str, str]] = []
    occupied: list[tuple[int, int]] = []

    def add(kind: str, match: re.Match[str]) -> None:
        span = match.span()
        if any(span[0] < end and start < span[1] for start, end in occupied):
            return
        value = match.group(0).strip()
        if kind == "IPV4":
            try:
                if ipaddress.ip_address(value).version != 4:
                    return
            except ValueError:
                return
        occupied.append(span)
        values.append((kind, value))

    for kind, pattern in (
        ("IPV4", _IPV4_PATTERN),
        ("VERSION", _VERSION_PATTERN),
        ("MEASUREMENT", _MEASUREMENT_PATTERN),
        ("PORT", _PORT_PATTERN),
    ):
        for match in pattern.finditer(answer):
            add(kind, match)

    question_text = _normalized_evidence(question)
    document_texts = [(key, _normalized_evidence(text)) for key, text in context.document_evidence]
    observations: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for kind, value in values:
        normalized = _normalized_evidence(value)
        key = (kind, normalized)
        if key in seen:
            continue
        seen.add(key)
        document_keys = [
            document_key
            for document_key, document_text in document_texts
            if normalized in document_text
        ]
        user_provided = normalized in question_text
        observations.append(
            {
                "kind": kind,
                "value": value,
                "status": (
                    "DOCUMENT"
                    if document_keys
                    else "USER_PROVIDED"
                    if user_provided
                    else "UNSUPPORTED"
                ),
                "document_keys": document_keys,
                "user_provided": user_provided,
            }
        )
    return observations


def evidence_violations(
    generated: GeneratedAnswer,
    context: DocumentContext,
) -> list[str]:
    document_keys = set(context.document_keys)
    image_map = {binding.key: binding for binding in context.image_bindings}
    used_documents = set(generated.used_document_keys)
    used_images = set(generated.used_image_keys)
    violations = [
        *(f"invalid_document:{key}" for key in sorted(used_documents - document_keys)),
        *(f"invalid_image:{key}" for key in sorted(used_images - set(image_map))),
    ]
    violations.extend(
        f"image_document_mismatch:{key}"
        for key in sorted(used_images & set(image_map))
        if image_map[key].document_key not in used_documents
    )
    return violations


def normalize_evidence_bindings(
    generated: GeneratedAnswer,
    context: DocumentContext,
) -> GeneratedAnswer:
    """Make valid image evidence imply its owning document without changing prose."""

    image_map = {binding.key: binding for binding in context.image_bindings}
    used_documents = list(generated.used_document_keys)
    for image_key in generated.used_image_keys:
        binding = image_map.get(image_key)
        if binding is not None and binding.document_key not in used_documents:
            used_documents.append(binding.document_key)
    if used_documents == generated.used_document_keys:
        return generated
    return GeneratedAnswer.model_validate(
        {**generated.model_dump(mode="python"), "used_document_keys": used_documents}
    )


def _content_blocks(
    answer: str,
    *,
    context: DocumentContext,
    used_images: tuple[str, ...],
    include_images: bool,
) -> tuple[str, list[ContentBlock]]:
    image_map = {binding.key: binding.asset_id for binding in context.image_bindings}
    blocks: list[ContentBlock] = []
    last_end = 0
    emitted: set[str] = set()
    for match in _IMAGE_MARKER_PATTERN.finditer(answer):
        text = clean_model_text(answer[last_end : match.start()].strip())
        if text:
            blocks.append(ContentBlock(type="text", text=text))
        marker_keys = [item.upper() for item in _IMAGE_KEY_PATTERN.findall(match.group(1))]
        for key in marker_keys:
            if include_images and key in used_images and key in image_map and key not in emitted:
                blocks.append(ContentBlock(type="image", image_asset_id=image_map[key]))
                emitted.add(key)
        last_end = match.end()
    tail = clean_model_text(answer[last_end:].strip())
    if tail:
        blocks.append(ContentBlock(type="text", text=tail))
    public_answer = clean_model_text(_IMAGE_MARKER_PATTERN.sub("", answer))
    if include_images:
        for key in used_images:
            if key in image_map and key not in emitted:
                blocks.append(ContentBlock(type="image", image_asset_id=image_map[key]))
    if not blocks and public_answer:
        blocks.append(ContentBlock(type="text", text=public_answer))
    return public_answer, blocks


def validate_generated_answer(
    generated: GeneratedAnswer,
    *,
    context: DocumentContext,
    include_images: bool,
    allowed_text: str = "",
) -> ChatResult:
    used_documents = set(generated.used_document_keys)
    used_images = tuple(dict.fromkeys(key.upper() for key in generated.used_image_keys))
    if violations := evidence_violations(generated, context):
        raise LlmError(
            "V2_EVIDENCE_INVALID",
            "模型引用了无效证据",
            retryable=False,
            detail=",".join(violations)[:500],
        )
    if leaks := public_leaks(
        generated.answer,
        context.title_aliases,
        allowed_text=allowed_text,
    ):
        raise LlmError(
            "V2_PUBLIC_LEAK",
            "回答包含内部实现信息",
            retryable=True,
            detail=",".join(leaks)[:500],
        )

    if generated.response_kind == "OUT_OF_SCOPE":
        return ChatResult(
            response_type=ResponseType.OUT_OF_SCOPE,
            answer_mode=AnswerMode.OUT_OF_SCOPE,
            answer=OUT_OF_SCOPE_MESSAGE,
            content=[ContentBlock(type="text", text=OUT_OF_SCOPE_MESSAGE)],
            disclaimer=None,
            valid_sources=[],
            citation_result={"status": "NOT_REQUIRED", "pipeline": "V2"},
        )

    answer, blocks = _content_blocks(
        generated.answer,
        context=context,
        used_images=used_images,
        include_images=include_images,
    )
    if not answer.strip() and not any(block.type == "image" for block in blocks):
        raise LlmError("LLM_EMPTY_RESPONSE", "模型未返回有效回答", retryable=True)

    if generated.response_kind == "ASK_ONE":
        response_type = ResponseType.UNCONFIRMED
        answer_mode = AnswerMode.UNCONFIRMED
    else:
        response_type = ResponseType.ANSWERED
        answer_mode = {
            "DOCUMENT": AnswerMode.PRODUCT_KNOWLEDGE,
            "DOCUMENT_AND_GENERAL": AnswerMode.PRODUCT_EXPLAINED,
            "GENERAL": AnswerMode.PRODUCT_GENERAL,
            "NONE": AnswerMode.UNCONFIRMED,
        }[generated.basis]
    return ChatResult(
        response_type=response_type,
        answer_mode=answer_mode,
        answer=answer,
        content=blocks,
        disclaimer=TECHNICAL_DISCLAIMER,
        valid_sources=[],
        citation_result={
            "status": "NOT_PUBLIC",
            "pipeline": "V2",
            "document_count": len(used_documents),
            "image_count": len(used_images) if include_images else 0,
        },
    )
