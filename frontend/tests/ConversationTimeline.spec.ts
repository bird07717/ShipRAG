import ElementPlus from "element-plus";
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import { adminApi } from "@/api/admin";
import ConversationTimeline from "@/components/ConversationTimeline.vue";
import type { RagTrace, SupportMessage, TraceSummary } from "@/types/admin";

describe("ConversationTimeline", () => {
  afterEach(() => vi.restoreAllMocks());

  it("keeps conversation and Trace access without workflow/escalation details", async () => {
    const messages: SupportMessage[] = [
      {
        id: "user-1",
        conversation_id: "conv-1",
        role: "USER",
        content: "设备出现故障怎么办？",
        sources: [],
        status: "COMPLETED",
        created_at: "2026-10-07T10:00:00Z",
        updated_at: "2026-10-07T10:00:00Z",
      },
      {
        id: "assistant-1",
        conversation_id: "conv-1",
        role: "ASSISTANT",
        content: "请先检查设备电源。",
        sources: [],
        status: "COMPLETED",
        response_type: "ANSWERED",
        created_at: "2026-10-07T10:00:05Z",
        updated_at: "2026-10-07T10:00:05Z",
      },
    ];
    const summary = {
      trace_id: "trace-1",
      mode: "CHAT",
      kb_id: "kb-1",
      index_id: "index-1",
      conversation_id: "conv-1",
      question: messages[0]!.content,
      status: "COMPLETED",
      latency: {},
      error: {},
      created_at: "2026-10-07T10:00:00Z",
    } satisfies TraceSummary;
    const trace = {
      ...summary,
      retrieval_result: {
        workflow_before: { current_step: 1 },
        workflow_after: { current_step: 2 },
        escalation: { required: true, reason: "需要工程师处理" },
      },
      rerank_result: {},
      selected_context: [],
      prompt: null,
      answer: "请先检查设备电源。",
      sources: [],
      citation_result: {},
      model_usage: {},
    } satisfies RagTrace;

    vi.spyOn(adminApi, "listConversationMessages").mockResolvedValue({ items: messages });
    vi.spyOn(adminApi, "listTraces").mockResolvedValue([summary]);
    vi.spyOn(adminApi, "getTrace").mockResolvedValue(trace);

    const wrapper = mount(ConversationTimeline, {
      props: { conversationId: "conv-1" },
      global: { plugins: [ElementPlus] },
    });
    await flushPromises();

    expect(wrapper.text()).toContain("设备出现故障怎么办？");
    expect(wrapper.text()).toContain("请先检查设备电源。");
    expect(wrapper.text()).toContain("查看完整 Trace");
    expect(wrapper.text()).not.toContain("工作流状态与升级详情");
    expect(wrapper.text()).not.toContain("本轮触发升级人工");
  });
});
