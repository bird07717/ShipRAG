/** Page sizes offered by every server-paged admin list. */
export const PAGE_SIZES = [20, 50, 100];

/**
 * After deletes or filters shrinking the result, the current page can lie
 * beyond the last one: the server returns no rows although `total > 0`.
 * Returns the last valid page to reload, or null when the page is fine.
 */
export function clampedPage(page: number, pageSize: number, itemCount: number, total: number): number | null {
  if (itemCount > 0 || total <= 0 || page <= 1) return null;
  const last = Math.max(1, Math.ceil(total / pageSize));
  return last < page ? last : null;
}
