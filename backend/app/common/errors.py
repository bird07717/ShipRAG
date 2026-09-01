from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ApiError(Exception):
    code: str
    message: str
    status_code: int
    details: dict[str, Any] = field(default_factory=dict)


# End-user copy for chat answer failures: the integrator forwards `message`
# verbatim into the embedded page, so server-side faults (HTTP 5xx) must not
# leak technical detail ("查询向量生成失败"/"Rerank 上游调用超时"/...). The
# machine-readable `code` and the trace/request_error_log keep full fidelity
# for diagnostics and the caller's retry logic.
SERVER_BUSY_MESSAGE = "服务繁忙，请稍后重试"


def user_safe_chat_message(exc: ApiError) -> str:
    """Chat-facing message for an ApiError.

    Server faults (5xx) collapse to the uniform busy message; client errors
    (4xx) keep their specific, actionable text (e.g. "问题不能为空").
    """
    if exc.status_code >= 500:
        return SERVER_BUSY_MESSAGE
    return exc.message
