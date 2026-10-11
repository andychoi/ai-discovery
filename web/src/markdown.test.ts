import { describe, expect, it } from "vitest";
import { markdownToHtml, rewriteHref } from "./markdown";

describe("markdown", () => {
  it("turns page-id links into SPA links and leaves external links external", () => {
    expect(rewriteHref("table:orders")).toEqual({ href: "/docs/table%3Aorders", internal: true });
    const html = markdownToHtml("[orders](table:orders) and [docs](https://example.com)");
    expect(html).toContain('<a href="/docs/table%3Aorders" data-internal="1">orders</a>');
    expect(html).toContain('href="https://example.com" rel="noopener noreferrer" target="_blank"');
  });

  it("keeps inline formatting inside link text and escapes it", () => {
    const html = markdownToHtml("[`a.B<T>.c`](symbol:a.B.c@src/B.java:3)");
    expect(html).toContain('data-internal="1"><code>a.B&lt;T&gt;.c</code></a>');
  });

  it("emits escaped mermaid placeholders and escaped code", () => {
    const html = markdownToHtml("```mermaid\ngraph TD; A-->B<script>\n```\n\n```java\nif (a < b) {}\n```");
    expect(html).toContain('<pre class="mermaid-src">graph TD; A--&gt;B&lt;script&gt;</pre>');
    expect(html).toContain("<pre><code>if (a &lt; b) {}</code></pre>");
  });

  it("renders GFM tables", () => {
    expect(markdownToHtml("| a | b |\n|---|---|\n| 1 | 2 |")).toContain("<table>");
  });
});
