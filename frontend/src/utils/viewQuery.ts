/*
 * Write a view's own UI state (tab, opened record) back into the hash so a
 * refresh, a copied link or the browser history restores it.
 *
 * Uses history.replaceState (no history entry), then notifies App.vue so its
 * navigationQuery stays equal to the URL. Views therefore see their own write
 * come back through navigationQuery and must treat an unchanged state as a
 * no-op. Writes only while `view` is the current hash view, so a view
 * rendered elsewhere never rewrites another page's URL.
 */
export const VIEW_QUERY_REPLACED_EVENT = "shipdemo:view-query-replaced";

export function replaceViewQuery(view: string, params: Record<string, string | null | undefined>): void {
  if (typeof window === "undefined") return;
  const current = window.location.hash;
  if (current !== `#/${view}` && !current.startsWith(`#/${view}?`)) return;
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value) search.set(key, value);
  }
  const query = search.toString();
  const target = `#/${view}${query ? `?${query}` : ""}`;
  if (current === target) return;
  window.history.replaceState(window.history.state, "", target);
  window.dispatchEvent(new Event(VIEW_QUERY_REPLACED_EVENT));
}
