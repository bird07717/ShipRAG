"""Offset pagination for admin list endpoints that return a bare JSON array.

The body stays a list (unchanged for existing callers); the total number of
rows matching the filters is returned in ``X-Total-Count`` so the management
console can page on the server. The header is exposed to cross-origin
callers in ``app.main``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sized
from typing import Annotated

from fastapi import Query, Response

TOTAL_COUNT_HEADER = "X-Total-Count"

OffsetQuery = Annotated[int, Query(ge=0, le=1_000_000)]


def set_total_count(response: Response, total: int) -> None:
    response.headers[TOTAL_COUNT_HEADER] = str(total)


async def paged_total(
    rows: Sized, *, limit: int | None, offset: int, count: Callable[[], Awaitable[int]]
) -> int:
    """Total matching rows for a page.

    A first page shorter than ``limit`` (or an unlimited list read from the
    start) already is the whole result, so the count query is skipped.
    """
    if offset == 0 and (limit is None or len(rows) < limit):
        return len(rows)
    return await count()
