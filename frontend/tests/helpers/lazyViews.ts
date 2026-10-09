/*
 * AdminStudio and App load views/DemoChat through defineAsyncComponent. Specs
 * that mount those shells warm the module cache first so the async loaders
 * resolve within flushPromises(), as an already-fetched chunk would. Kept out
 * of the global setup so per-file vi.mock() of these modules still applies.
 */
export async function preloadLazyViews(): Promise<void> {
  await Promise.all([
    import("@/views/KnowledgeView.vue"),
    import("@/views/DocumentsView.vue"),
    import("@/views/IndexBuildView.vue"),
    import("@/views/LogsView.vue"),
    import("@/views/PlaygroundView.vue"),
    import("@/views/ConfigurationView.vue"),
    import("@/views/EngineRoutingView.vue"),
    import("@/views/SupportReviewView.vue"),
    import("@/views/ConversationsView.vue"),
    import("@/components/DemoChat.vue"),
  ]);
}
