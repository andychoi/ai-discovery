import { describe, expect, it } from "vitest";
import { drilldown, drillLabel } from "./drilldown";

const flowPage = {
  type: "flow",
  links: [
    { dst: "entity:com.x.Order", kind: "writes" },
    { dst: "table:orders", kind: "writes" },
    { dst: "symbol:com.x.OrderService.create", kind: "calls" },
  ],
  backlinks: [
    { src: "screen:orders-new", kind: "routes_to" },
    { src: "domain:orders", kind: "link" },
  ],
};

describe("drilldown", () => {
  it("groups business-level links: deeper levels first, then the levels above", () => {
    const groups = drilldown(flowPage);
    expect(groups.map((g) => [g.type, g.direction])).toEqual([
      ["entity", "down"], ["table", "down"], ["screen", "up"],
    ]);
    expect(groups[2].items).toEqual([{ id: "screen:orders-new", kind: "routes_to" }]);
  });

  it("ignores non-business pages and its own level, and de-duplicates link + backlink", () => {
    const groups = drilldown({
      type: "entity",
      links: [{ dst: "table:orders", kind: "see_also" }, { dst: "entity:com.x.Line", kind: "see_also" }],
      backlinks: [{ src: "table:orders", kind: "see_also" }, { src: "flow:com.x.A.b", kind: "writes" }],
    });
    expect(groups.map((g) => [g.type, g.items.length, g.direction])).toEqual([["table", 1, "down"], ["flow", 1, "up"]]);
  });

  it("treats every level as below a page outside the chain (a domain or symbol page)", () => {
    const groups = drilldown({ type: "domain", links: [{ dst: "screen:s", kind: "link" }, { dst: "table:t", kind: "link" }], backlinks: [] });
    expect(groups.every((g) => g.direction === "down")).toBe(true);
    expect(drilldown({ type: "symbol", links: [], backlinks: [] })).toEqual([]);
  });

  it("labels flows and entities by Class.method and class name", () => {
    expect(drillLabel("flow:com.x.OrderController.create")).toBe("OrderController.create");
    expect(drillLabel("entity:com.x.Order")).toBe("Order");
    expect(drillLabel("screen:orders-new")).toBe("orders-new");
  });
});
