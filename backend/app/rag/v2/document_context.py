from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.common.text import estimate_tokens
from app.rag.v2.document_retrieval import DocumentCandidate


@dataclass(frozen=True, slots=True)
class ImageBinding:
    key: str
    document_key: str
    asset_id: str


@dataclass(frozen=True, slots=True)
class DocumentContext:
    prompt_context: str
    document_keys: tuple[str, ...]
    image_bindings: tuple[ImageBinding, ...]
    title_aliases: tuple[str, ...]
    document_ids: tuple[str, ...]
    estimated_tokens: int
    context_mode: str
    document_evidence: tuple[tuple[str, str], ...] = ()


def _render_element(element: dict[str, Any], image_key: str | None) -> str:
    kind = str(element.get("element_type") or "TEXT").upper()
    content = str(element.get("content") or "").strip()
    if kind == "IMAGE":
        caption = str(element.get("image_caption") or content).strip()
        return f"[图片 {image_key}] {caption}" if image_key else ""
    label = "表格" if kind == "TABLE" else "正文"
    return f"[{label}]\n{content}" if content else ""


async def build_document_context(
    repository: Any,
    *,
    knowledge_id: Any,
    index_id: Any,
    candidates: list[DocumentCandidate],
    token_budget: int,
) -> DocumentContext:
    rendered_documents: list[str] = []
    document_keys: list[str] = []
    image_bindings: list[ImageBinding] = []
    title_aliases: list[str] = []
    document_ids: list[str] = []
    document_evidence: list[tuple[str, str]] = []
    used_tokens = 0
    context_mode = "FULL_DOCUMENT"
    image_number = 1

    for document_number, candidate in enumerate(candidates, start=1):
        document = await repository.get_document_blocks(
            knowledge_id=knowledge_id,
            index_id=index_id,
            document_id=candidate.document_id,
        )
        source = await repository.get_document_source(candidate.document_id)
        if str(source.get("knowledge_id")) != str(knowledge_id):
            raise ValueError("RAG V2 candidate escaped the frozen knowledge base")
        document_key = f"D{document_number}"
        element_lines: list[str] = []
        candidate_images: list[ImageBinding] = []
        for element in document.get("elements") or []:
            asset_id = element.get("image_asset_id")
            image_key = None
            if asset_id is not None:
                image_key = f"I{image_number}"
                image_number += 1
                candidate_images.append(ImageBinding(image_key, document_key, str(asset_id)))
            rendered = _render_element(element, image_key)
            if rendered:
                element_lines.append(rendered)
        complete = (
            f'<document key="{document_key}">\n' + "\n\n".join(element_lines) + "\n</document>"
        )
        document_tokens = estimate_tokens(complete)
        if used_tokens + document_tokens <= token_budget:
            rendered = complete
            retained_images = candidate_images
        elif not rendered_documents:
            context_mode = "SECTION_FALLBACK"
            retained_lines: list[str] = []
            retained_images = []
            for line in element_lines:
                trial = (
                    f'<document key="{document_key}" mode="SECTION_FALLBACK">\n'
                    + "\n\n".join([*retained_lines, line])
                    + "\n</document>"
                )
                if estimate_tokens(trial) > token_budget:
                    break
                retained_lines.append(line)
                for binding in candidate_images:
                    if f"[图片 {binding.key}]" in line:
                        retained_images.append(binding)
            rendered = (
                f'<document key="{document_key}" mode="SECTION_FALLBACK">\n'
                + "\n\n".join(retained_lines)
                + "\n</document>"
            )
            if not retained_lines:
                break
            document_tokens = estimate_tokens(rendered)
        else:
            break

        rendered_documents.append(rendered)
        document_keys.append(document_key)
        document_evidence.append((document_key, rendered))
        image_bindings.extend(retained_images)
        document_ids.append(str(candidate.document_id))
        used_tokens += document_tokens
        for alias in (document.get("title"), source.get("display_name"), source.get("filename")):
            value = str(alias or "").strip()
            if value and value not in title_aliases:
                title_aliases.append(value)

    return DocumentContext(
        prompt_context="\n\n".join(rendered_documents),
        document_keys=tuple(document_keys),
        image_bindings=tuple(image_bindings),
        title_aliases=tuple(title_aliases),
        document_ids=tuple(document_ids),
        estimated_tokens=used_tokens,
        context_mode=context_mode,
        document_evidence=tuple(document_evidence),
    )
