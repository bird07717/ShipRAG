"""Hide model control markers while preserving ordinary Markdown and text."""

from __future__ import annotations

import re

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MARKER = re.compile(r"\[(?:MODE:[A-Z_]+|IMG:S\d+|S\d+)\]")


def _possible_marker(value: str) -> bool:
    return any(prefix.startswith(value) for prefix in ("[MODE:", "[IMG:S", "[S")) or bool(
        re.fullmatch(r"\[(?:MODE:[A-Z_]*|IMG:S\d*|S\d*)", value)
    )


class ModelTextStream:
    """Hold only a potential split marker; never buffer the whole answer."""

    def __init__(self) -> None:
        self.pending = ""
        self.started = False

    def feed(self, chunk: str, *, final: bool = False) -> str:
        text = self.pending + _CONTROL.sub("", chunk)
        self.pending = ""
        output: list[str] = []
        pos = 0
        while pos < len(text):
            if text[pos] != "[":
                output.append(text[pos])
                pos += 1
                continue
            match = _MARKER.match(text, pos)
            if match:
                pos = match.end()
                continue
            tail = text[pos:]
            if len(tail) <= 64 and _possible_marker(tail):
                if not final:
                    self.pending = tail
                elif tail == "[":
                    output.append(tail)
                break
            output.append("[")
            pos += 1
        value = "".join(output)
        if not self.started:
            value = value.lstrip()
            self.started = bool(value)
        return value


def clean_model_text(text: str) -> str:
    return ModelTextStream().feed(text, final=True).strip()


_GENERAL_HEADING = re.compile(r"(?m)^\s*\*\*通用建议\*\*\s*[:：]?[^\S\n]*$")


def separate_general_advice(answer: str) -> tuple[str, bool]:
    """The final general-advice section cannot borrow document citations/images."""
    match = _GENERAL_HEADING.search(answer)
    if match is None:
        return answer, False
    return answer[: match.end()] + _MARKER.sub("", answer[match.end() :]), True
