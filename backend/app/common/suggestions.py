"""Validation shared by admin schemas and publication checks."""

import unicodedata


def validate_suggestions(values: list[str]) -> list[str]:
    if len(values) > 3:
        raise ValueError("最多配置三条推荐问题")
    result = [value.strip() for value in values]
    if any(not 4 <= len(value) <= 200 for value in result):
        raise ValueError("每条推荐问题须为 4 至 200 字")
    keys = [
        "".join(unicodedata.normalize("NFKC", value).casefold().split()).rstrip("?？")
        for value in result
    ]
    if len(set(keys)) != len(keys):
        raise ValueError("推荐问题不能重复")
    return result
