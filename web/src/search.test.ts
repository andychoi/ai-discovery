import { describe, expect, it } from "vitest";
import { initialPalette, paletteReducer, snippetHtml, type Hit } from "./search";

const hit = (id: string): Hit => ({ page_id: id, title: id, type: "table", snippet: "", score: 1, source: "fts" });

describe("search palette", () => {
  it("ignores results for a stale query", () => {
    let s = paletteReducer(initialPalette, { type: "open" });
    s = paletteReducer(s, { type: "query", query: "ord" });
    s = paletteReducer(s, { type: "query", query: "orders" });
    s = paletteReducer(s, { type: "results", query: "ord", hits: [hit("table:a")] });
    expect(s.hits).toEqual([]);
    s = paletteReducer(s, { type: "results", query: "orders", hits: [hit("table:orders"), hit("table:b")] });
    expect(s.hits.map((h) => h.page_id)).toEqual(["table:orders", "table:b"]);
  });

  it("wraps keyboard movement and resets on close / empty query", () => {
    let s = paletteReducer({ ...initialPalette, open: true, query: "x" }, { type: "results", query: "x", hits: [hit("a:1"), hit("b:2")] });
    s = paletteReducer(s, { type: "move", delta: -1 });
    expect(s.active).toBe(1);
    s = paletteReducer(s, { type: "move", delta: 1 });
    expect(s.active).toBe(0);
    expect(paletteReducer(s, { type: "query", query: "  " }).hits).toEqual([]);
    expect(paletteReducer(s, { type: "close" })).toEqual(initialPalette);
  });

  it("escapes snippet text and marks matches", () => {
    expect(snippetHtml("<b>[orders]</b> & [x]")).toBe("&lt;b&gt;<mark>orders</mark>&lt;/b&gt; &amp; <mark>x</mark>");
  });
});
