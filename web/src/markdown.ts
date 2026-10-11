// Markdown → HTML for page bodies. Pure (no DOM): page-id links become SPA
// links, mermaid fences become placeholders rendered later by the reader.
// Sanitising (DOMPurify) happens in the browser, see PageView.
import { Marked } from "marked";
import { docHref, isPageId } from "./router";

const esc = (s: string) =>
  s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

export function rewriteHref(href: string): { href: string; internal: boolean } {
  if (isPageId(href)) return { href: docHref(href), internal: true };
  return { href, internal: false };
}

const md = new Marked({
  gfm: true,
  renderer: {
    link({ href, tokens }) {
      const { href: to, internal } = rewriteHref(href);
      const label = this.parser.parseInline(tokens); // keeps `code`/**bold** inside link text, escaped
      return internal
        ? `<a href="${esc(to)}" data-internal="1">${label}</a>`
        : `<a href="${esc(to)}" rel="noopener noreferrer" target="_blank">${label}</a>`;
    },
    code({ text, lang }) {
      if ((lang ?? "").trim() === "mermaid") return `<pre class="mermaid-src">${esc(text)}</pre>`;
      return `<pre><code>${esc(text)}</code></pre>`;
    },
  },
});

export function markdownToHtml(source: string): string {
  return md.parse(source, { async: false }) as string;
}
