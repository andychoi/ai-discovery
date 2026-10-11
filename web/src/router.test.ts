import { describe, expect, it } from "vitest";
import { docHref, isPageId, parseRoute, searchHref } from "./router";

describe("router", () => {
  it("recognises page ids by type prefix", () => {
    expect(isPageId("table:orders")).toBe(true);
    expect(isPageId("file:src/a/B.java")).toBe(true);
    expect(isPageId("symbol:com.x.Y.m@src/Y.java:12")).toBe(true);
    expect(isPageId("https://example.com")).toBe(false);
    expect(isPageId("table:")).toBe(false);
    expect(isPageId("nope:x")).toBe(false);
  });

  it("round-trips deep links, including ids with slashes and colons", () => {
    for (const id of ["table:orders", "file:src/a b/C.java", "symbol:a.B.c@src/B.java:3"]) {
      const href = docHref(id);
      const url = new URL(href, "http://x");
      expect(parseRoute(url.pathname, url.search)).toEqual({ name: "doc", pageId: id });
    }
  });

  it("maps the overview to / and parses search + unknown routes", () => {
    expect(docHref("overview:system")).toBe("/");
    expect(parseRoute("/")).toEqual({ name: "overview" });
    const s = new URL(searchHref("a&b c"), "http://x");
    expect(parseRoute(s.pathname, s.search)).toEqual({ name: "search", q: "a&b c" });
    expect(parseRoute("/docs/not-an-id")).toEqual({ name: "unknown", path: "/docs/not-an-id" });
    expect(parseRoute("//")).toEqual({ name: "overview" });
    expect(parseRoute("/search/", "?q=x")).toEqual({ name: "search", q: "x" });
    expect(parseRoute("/wat")).toEqual({ name: "unknown", path: "/wat" });
  });
});
