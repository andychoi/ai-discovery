import { describe, expect, it } from "vitest";
import { coverageText, impactMarkdown, isLowConfidence, legendKinds, shortLabel, toElements,
         type Neighborhood } from "./graphModel";
import { graphHref, impactHref, parseRoute } from "./router";

const N: Neighborhood = {
  focus: 1,
  nodes: [
    { id: 1, label: "com.x.OrderService.create", type: "method", file: "a.java", line: 3 },
    { id: 2, label: "com.x.OrderRepository.save", type: "method", file: "b.java", line: 9 },
    { id: 3, label: "external::stripe", type: "external_system", file: null, line: null },
  ],
  edges: [
    { src: 1, dst: 2, edge_kind: "calls", origin: "receiver_typed", confidence: 0.93, evidence_kind: "static" },
    { src: 1, dst: 3, edge_kind: "calls_external", origin: "external_client", confidence: 0.9, evidence_kind: "framework" },
    { src: 2, dst: 1, edge_kind: "calls", origin: "global_unique", confidence: 0.5, evidence_kind: "static" },
    { src: 1, dst: 99, edge_kind: "calls", origin: "self_scope", confidence: 0.95, evidence_kind: "static" },
  ],
};

describe("graph model", () => {
  it("builds cytoscape elements: focus marked, low-confidence edges classed, dangling edges dropped", () => {
    const els = toElements(N);
    const nodes = els.filter((e) => !("source" in e.data));
    const edges = els.filter((e) => "source" in e.data);
    expect(nodes.map((n) => n.classes)).toEqual(["focus t-method", "t-method", "t-external_system"]);
    expect(nodes[0]!.data.label).toBe("OrderService.create");
    expect(edges).toHaveLength(3);
    expect(edges.filter((e) => e.classes === "low").map((e) => e.data.origin)).toEqual(["global_unique"]);
    expect(isLowConfidence(N.edges[2]!)).toBe(true);
  });

  it("reports the server's true totals", () => {
    expect(coverageText(200, 3412)).toBe("showing 200 of 3,412 nodes");
    expect(coverageText(12, 12, "edges")).toBe("12 edges");
  });

  it("lists evidence kinds present, known ones first", () => {
    expect(legendKinds(N)).toEqual(["static", "framework"]);
    expect(shortLabel("external::stripe")).toBe("stripe");
  });

  it("renders IMPACT_REPORT.md with items, entry points and unknowns", () => {
    const md = impactMarkdown({
      target: "com.x.OrderService.create", depth: 3, entry_points_reached: ["endpoint:POST-/orders"],
      items: [{ node: "com.x.OrderController.create", kind: "endpoint", score: 0.744, depth: 1, via: [],
                certainty: "likely", citation: { path: "src/C.java", line_start: 26, line_end: 29 } }],
      unknowns: [{ node: "com.x.Jobs.run", reason: "dynamic", call: "invoke" }],
    }, { items: 5 });
    expect(md).toContain("# Impact report: `com.x.OrderService.create`");
    expect(md).toContain("1 item(s) of 5.");
    expect(md).toContain("| 1 | `com.x.OrderController.create` | endpoint | 0.74 | 1 | likely | `src/C.java:26` |");
    expect(md).toContain("- `endpoint:POST-/orders`");
    expect(md).toContain("- `com.x.Jobs.run` dynamic (`invoke`)");
  });

  it("routes /graph and /impact", () => {
    const g = new URL(graphHref("com.x.A.b", 2), "http://x");
    expect(parseRoute(g.pathname, g.search)).toEqual({ name: "graph", focus: "com.x.A.b", depth: 2 });
    expect(parseRoute("/graph", "?focus=a&depth=7")).toEqual({ name: "graph", focus: "a", depth: 1 });
    const i = new URL(impactHref("table:orders"), "http://x");
    expect(parseRoute(i.pathname, i.search)).toEqual({ name: "impact", target: "table:orders" });
  });
});
