from __future__ import annotations

import re

from app.common.text import estimate_tokens
from app.ingestion.models import ChunkDraft, ParentChunkDraft, ParsedElement

__all__ = ["estimate_tokens"]

_TERMINAL_PUNCTUATION = ("。", "\uff01", "？", "!", "?", ".", "\uff1b", ";", "：", ":")
_SUSPENDED_END_PATTERN = re.compile(
    r"(?:点击|选择|设置为|输入|打开|进入|调整|按下|然后|如下|具体如下|步骤如下|"
    r"分别为|包括|例如|完成后|调整完成后|参见|如图|见下图)\s*[，,、\uff1b;：:]?$",
    flags=re.IGNORECASE,
)
_FOLLOWING_CONTENT_PATTERN = re.compile(
    r"(?:如下|具体如下|步骤如下|分别为|包括|例如|参见|如图|见下图)\s*[，,、\uff1b;：:]?$",
    flags=re.IGNORECASE,
)
_PROCEDURAL_PATTERN = re.compile(
    r"(?:^|[\n。\uff1b;])\s*(?:第?[一二三四五六七八九十百\d]+[步、.\uff0e)\uff09]|步骤\s*[一二三四五六七八九十\d]+)"
    r"|(?:点击|选择|设置|输入|打开|进入|连接|调整|配置|按下|确认|保存|上传|下载|重启)",
    flags=re.IGNORECASE,
)


def _section_prefix(section_path: list[str]) -> str:
    return f"章节：{' > '.join(section_path)}\n" if section_path else ""


def _text_chunk(
    elements: list[ParsedElement], indexes: list[int], target_chars: int, max_chars: int
) -> ChunkDraft:
    section_path = list(elements[indexes[0]].section_path)
    body = "\n\n".join(elements[index].content for index in indexes)
    content = f"{_section_prefix(section_path)}{body}".strip()
    return ChunkDraft(
        chunk_type="TEXT",
        content=content,
        search_text=content,
        section_path=section_path,
        element_indexes=indexes,
        token_count=estimate_tokens(content),
        metadata={
            "chunking_config": {"target_chars": target_chars, "max_chars": max_chars},
            "strategy": "section_paragraph",
        },
    )


def _text_chunks(
    elements: list[ParsedElement],
    indexes: list[int],
    target_chars: int,
    max_chars: int,
    overlap_paragraphs: int,
) -> list[ChunkDraft]:
    chunks: list[ChunkDraft] = []
    start = 0
    while start < len(indexes):
        end = start
        body_chars = 0
        while end < len(indexes):
            element_chars = len(elements[indexes[end]].content)
            if end > start and body_chars + element_chars > max_chars:
                break
            body_chars += element_chars
            end += 1
            if body_chars >= target_chars:
                break
        if end == start:
            end += 1
        window = indexes[start:end]
        chunks.append(_text_chunk(elements, window, target_chars, max_chars))
        if end >= len(indexes):
            break
        retained = min(overlap_paragraphs, max(0, len(window) - 1))
        next_start = end - retained
        start = next_start if next_start > start else end
    return chunks


def _table_chunks(
    element: ParsedElement, index: int, target_chars: int, max_chars: int
) -> list[ChunkDraft]:
    lines = element.content.splitlines()
    if len(element.content) <= max_chars or len(lines) <= 2:
        bodies = [element.content]
    else:
        header = lines[:2]
        bodies = []
        current = header.copy()
        for row in lines[2:]:
            candidate = "\n".join([*current, row])
            if len(candidate) > max_chars and len(current) > 2:
                bodies.append("\n".join(current))
                current = [*header, row]
            else:
                current.append(row)
        if len(current) > 2:
            bodies.append("\n".join(current))
    chunks: list[ChunkDraft] = []
    for part_no, body in enumerate(bodies, start=1):
        content = f"{_section_prefix(element.section_path)}{body}".strip()
        chunks.append(
            ChunkDraft(
                chunk_type="TABLE",
                content=content,
                search_text=content.replace("|", " ").replace("---", " "),
                section_path=list(element.section_path),
                element_indexes=[index],
                token_count=estimate_tokens(content),
                metadata={
                    "chunking_config": {
                        "target_chars": target_chars,
                        "max_chars": max_chars,
                    },
                    "strategy": "table_rows",
                    "part_no": part_no,
                    "part_count": len(bodies),
                },
            )
        )
    return chunks


def _image_chunk(elements: list[ParsedElement], index: int) -> ChunkDraft:
    image = elements[index]
    adjacent_indexes: list[int] = []
    if index > 0 and elements[index - 1].element_type == "TEXT":
        adjacent_indexes.append(index - 1)
    adjacent_indexes.append(index)
    if index + 1 < len(elements) and elements[index + 1].element_type == "TEXT":
        adjacent_indexes.append(index + 1)

    nearby_text = [
        elements[element_index].content
        for element_index in adjacent_indexes
        if elements[element_index].element_type == "TEXT"
    ]
    image_description = image.content or "文档内图片"
    body_parts = [*nearby_text, f"图片：{image_description}"]
    body = "\n\n".join(body_parts)
    content = f"{_section_prefix(image.section_path)}{body}".strip()
    return ChunkDraft(
        chunk_type="MIXED" if nearby_text else "IMAGE",
        content=content,
        search_text=content,
        section_path=list(image.section_path),
        element_indexes=adjacent_indexes,
        token_count=estimate_tokens(content),
        metadata={"strategy": "adjacent_image", "image_element_index": index},
        image_bytes=image.image_bytes,
        image_mime_type=image.image_mime_type,
    )


def _classify_chunk(elements: list[ParsedElement], chunk: ChunkDraft) -> None:
    body = chunk.content.strip()
    reasons: list[str] = []
    if _SUSPENDED_END_PATTERN.search(body):
        reasons.append("TRAILING_SUSPENDED_PHRASE")
    if body and not body.endswith(_TERMINAL_PUNCTUATION):
        reasons.append("MISSING_TERMINAL_PUNCTUATION")
    last_index = max(chunk.element_indexes)
    if last_index + 1 < len(elements) and elements[last_index + 1].element_type == "IMAGE":
        reasons.append("FOLLOWED_BY_IMAGE")
    if _FOLLOWING_CONTENT_PATTERN.search(body):
        reasons.append("REFERENCES_FOLLOWING_CONTENT")
    chunk.incomplete_reasons = list(dict.fromkeys(reasons))
    chunk.suspected_incomplete = bool(chunk.incomplete_reasons)
    chunk.is_procedural = bool(_PROCEDURAL_PATTERN.search(body))
    chunk.metadata.update(
        {
            "suspected_incomplete": chunk.suspected_incomplete,
            "incomplete_reasons": chunk.incomplete_reasons,
            "is_procedural": chunk.is_procedural,
        }
    )


def build_chunks(
    elements: list[ParsedElement],
    target_chars: int = 1_200,
    max_chars: int = 2_000,
    overlap_paragraphs: int = 1,
) -> list[ChunkDraft]:
    chunks: list[ChunkDraft] = []
    index = 0
    while index < len(elements):
        element = elements[index]
        if element.element_type == "TEXT":
            text_indexes = [index]
            index += 1
            while (
                index < len(elements)
                and elements[index].element_type == "TEXT"
                and elements[index].section_path == element.section_path
            ):
                text_indexes.append(index)
                index += 1
            chunks.extend(
                _text_chunks(
                    elements,
                    text_indexes,
                    target_chars,
                    max_chars,
                    overlap_paragraphs,
                )
            )
        elif element.element_type == "TABLE":
            chunks.extend(_table_chunks(element, index, target_chars, max_chars))
            index += 1
        elif element.element_type == "IMAGE":
            chunks.append(_image_chunk(elements, index))
            index += 1

    if not chunks:
        raise ValueError("文档未生成任何 Chunk")
    mapped = {index for chunk in chunks for index in chunk.element_indexes}
    missing = set(range(len(elements))) - mapped
    if missing:
        raise ValueError(f"存在未映射 Element: {sorted(missing)}")
    for chunk in chunks:
        _classify_chunk(elements, chunk)
    return chunks


def build_parent_chunks(
    elements: list[ParsedElement],
    chunks: list[ChunkDraft],
    *,
    max_chars: int = 8_000,
) -> list[ParentChunkDraft]:
    if max_chars <= 0:
        raise ValueError("Parent Chunk 字符上限必须为正数")
    parents: list[ParentChunkDraft] = []
    child_group: list[int] = []
    element_group: list[int] = []
    section_path: list[str] = []

    def flush_parent() -> None:
        if not child_group:
            return
        ordered_elements = list(dict.fromkeys(element_group))
        body = "\n\n".join(
            elements[item].content for item in ordered_elements if elements[item].content
        )
        content = f"{_section_prefix(section_path)}{body}".strip()
        parent_index = len(parents)
        parents.append(
            ParentChunkDraft(
                parent_type="SECTION",
                content=content,
                section_path=list(section_path),
                element_indexes=ordered_elements,
                child_indexes=list(child_group),
                token_count=estimate_tokens(content),
                metadata={"strategy": "section_window", "max_chars": max_chars},
            )
        )
        for child_index in child_group:
            chunks[child_index].parent_index = parent_index
        child_group.clear()
        element_group.clear()

    for child_index, chunk in enumerate(chunks):
        unique_new_elements = [item for item in chunk.element_indexes if item not in element_group]
        added_chars = sum(len(elements[item].content) for item in unique_new_elements)
        section_changed = bool(child_group) and chunk.section_path != section_path
        would_exceed = bool(child_group) and (
            sum(len(elements[item].content) for item in element_group) + added_chars > max_chars
        )
        if section_changed or would_exceed:
            flush_parent()
        if not child_group:
            section_path = list(chunk.section_path)
        child_group.append(child_index)
        element_group.extend(unique_new_elements)
    flush_parent()

    section_counts: dict[tuple[str, ...], int] = {}
    for parent in parents:
        key = tuple(parent.section_path)
        section_counts[key] = section_counts.get(key, 0) + 1
    for parent in parents:
        if section_counts[tuple(parent.section_path)] > 1:
            parent.parent_type = "SECTION_WINDOW"

    for parent in parents:
        for position, child_index in enumerate(parent.child_indexes):
            chunk = chunks[child_index]
            chunk.previous_chunk_index = (
                parent.child_indexes[position - 1] if position > 0 else None
            )
            chunk.next_chunk_index = (
                parent.child_indexes[position + 1]
                if position + 1 < len(parent.child_indexes)
                else None
            )
    return parents
