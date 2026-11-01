// Pure helpers for the graph explorer and impact view (roadmap item M4.4.a) — tested without a DOM.

export interface GraphNode { id: number; label: string; type: string; file: string | null; line: number | null }
export interface GraphEdge {
  src: number; dst: number; edge_kind: string; origin: string; confidence: number; evidence_kind: string;
}
export interface Neighborhood { focus: number; nodes: GraphNode[]; edges: GraphEdge[] }

/** Below this an edge is a guess (ADR-0004: flows never follow it) — drawn dashed and faded. */
export const LOW_CONFIDENCE = 0.75;

export const EVIDENCE_COLORS: Record<string, string> = {
  static: "#3b6fd8",      // parsed code
  framework: "#8a4fd0",   // framework binding (DI, routes, markup)
  scip: "#1f9d6b",        // compiler-grade index
  runtime: "#d07a1f",     // observed (traces/coverage overlays)
};
export const EDGE_FALLBACK_COLOR = "#7a8494";

export function isLowConfidence(e: GraphEdge): boolean {
  return e.confidence < LOW_CONFIDENCE;
}

export function shortLabel(qualified: string): string {
  const tail = qualified.split("::").pop() ?? qualified;
  const parts = tail.split(".");
  return parts.length >= 2 ? `${parts[parts.length - 2]}.${parts[parts.length - 1]}` : tail;
}

export interface CyElement { data: Record<string, unknown>; classes?: string }

/** Cytoscape elements: the focus node and low-confidence edges get classes the stylesheet styles. */
export function toElements(n: Neighborhood): CyElement[] {
  const ids = new Set(n.nodes.map((x) => x.id));
  const nodes = n.nodes.map((x) => ({
    data: { id: String(x.id), label: shortLabel(x.label), full: x.label, type: x.type },
    classes: [x.id === n.focus ? "focus" : "", `t-${x.type}`].filter(Boolean).join(" "),
  }));
  const edges = n.edges.filter((e) => ids.has(e.src) && ids.has(e.dst)).map((e) => ({
    data: { id: `${e.src}->${e.dst}:${e.edge_kind}`, source: String(e.src), target: String(e.dst),
            color: EVIDENCE_COLORS[e.evidence_kind] ?? EDGE_FALLBACK_COLOR, evidence: e.evidence_kind,
            origin: e.origin, confidence: e.confidence, kind: e.edge_kind },
    classes: isLowConfidence(e) ? "low" : "",
  }));
  return [...nodes, ...edges];
}

/** "showing 200 of 3,412 nodes" — the server's true totals, never the rendered count alone. */
export function coverageText(shown: number, total: number, what = "nodes"): string {
  const fmt = (v: number) => v.toLocaleString("en-US");
  return shown >= total ? `${fmt(total)} ${what}` : `showing ${fmt(shown)} of ${fmt(total)} ${what}`;
}

/** Evidence kinds present in a neighborhood, in legend order. */
export function legendKinds(n: Neighborhood): string[] {
  const seen = new Set(n.edges.map((e) => e.evidence_kind));
  return [...Object.keys(EVIDENCE_COLORS).filter((k) => seen.has(k)), ...[...seen].filter((k) => !(k in EVIDENCE_COLORS)).sort()];
}

// ── impact ──────────────────────────────────────────────────────────────────

export interface ImpactVia { src: string; dst: string; edge_kind: string; origin: string; confidence: number }
export interface ImpactItem {
  node: string; kind: string; page_id?: string | null; score: number; depth: number; via: ImpactVia[];
  may_break?: boolean; certainty?: string; citation?: { path: string; line_start: number; line_end: number } | null;
}
export interface ImpactData {
  target: string; depth: number; items: ImpactItem[]; entry_points_reached: string[];
  tests_reached?: string[]; unknowns: { node?: string; reason?: string; call?: string; detail?: string }[];
  note?: string;
}

/** IMPACT_REPORT.md: the same ranked list an agent gets from MCP get_impact. */
export function impactMarkdown(d: ImpactData, totals: Record<string, unknown> = {}): string {
  const lines = [`# Impact report: \`${d.target}\``, "", `Depth ${d.depth}. ${d.items.length} item(s)` +
    (typeof totals.items === "number" && totals.items > d.items.length ? ` of ${totals.items}` : "") + ".", ""];
  if (d.note) lines.push(`> ${d.note}`, "");
  if (d.items.length) {
    lines.push("| # | Item | Kind | Score | Depth | Certainty | Where |", "|---|---|---|---|---|---|---|");
    d.items.forEach((it, i) => {
      const where = it.citation ? `\`${it.citation.path}:${it.citation.line_start}\`` : "";
      lines.push(`| ${i + 1} | \`${it.node}\` | ${it.kind} | ${it.score.toFixed(2)} | ${it.depth} | ${it.certainty ?? ""} | ${where} |`);
    });
    lines.push("");
  }
  if (d.entry_points_reached.length) lines.push("## Entry points reached", "", ...d.entry_points_reached.map((e) => `- \`${e}\``), "");
  if (d.tests_reached?.length) lines.push("## Tests reached", "", ...d.tests_reached.map((t) => `- \`${t}\``), "");
  lines.push("## Unknowns", "");
  if (d.unknowns.length) {
    for (const u of d.unknowns) lines.push(`- ${u.node ? `\`${u.node}\`` : ""} ${u.reason ?? ""} ${u.call ? `(\`${u.call}\`)` : ""} ${u.detail ?? ""}`.replace(/\s+/g, " ").trimEnd());
  } else {
    lines.push("- none recorded (unresolved calls on the path would be listed here)");
  }
  return lines.join("\n") + "\n";
}
