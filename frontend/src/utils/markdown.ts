import MarkdownIt from "markdown-it";
import DOMPurify from "dompurify";

const md = new MarkdownIt({
  html: false,
  breaks: true,
  linkify: false,
});

const IMG_MARKER_PATTERN = /\[IMG:S\d+\]/g;

export function stripImageMarkers(text: string): string {
  return text.replace(IMG_MARKER_PATTERN, "");
}

export function renderMarkdown(text: string): string {
  if (!text) return "";
  return DOMPurify.sanitize(md.render(stripImageMarkers(text)));
}
