import { describe, expect, it } from "vitest";

import { renderMarkdown, stripImageMarkers } from "@/utils/markdown";

describe("stripImageMarkers", () => {
  it("removes [IMG:Sn] markers", () => {
    expect(stripImageMarkers("见下图 [IMG:S1] 和 [IMG:S12]")).toBe("见下图  和 ");
  });

  it("leaves citation markers intact", () => {
    expect(stripImageMarkers("步骤 [S1] 与 [S2]")).toBe("步骤 [S1] 与 [S2]");
  });
});

describe("renderMarkdown", () => {
  it("renders bold and ordered lists", () => {
    const html = renderMarkdown("**前置条件**\n\n1. 用网线连接\n2. 设置 IP");
    expect(html).toContain("<strong>前置条件</strong>");
    expect(html).toContain("<ol>");
    expect(html).toContain("<li>用网线连接</li>");
  });

  it("converts single newlines to line breaks", () => {
    const html = renderMarkdown("第一行\n第二行");
    expect(html).toContain("<br>");
  });

  it("strips image markers before rendering", () => {
    const html = renderMarkdown("说明 [IMG:S1] 结束");
    expect(html).not.toContain("IMG:S1");
  });

  it("escapes raw html and sanitizes script tags", () => {
    const html = renderMarkdown("<script>alert(1)</script>正文");
    expect(html).not.toContain("<script>");
    expect(html).toContain("正文");
  });

  it("returns empty string for empty input", () => {
    expect(renderMarkdown("")).toBe("");
  });
});
