// Graph explorer (roadmap item M4.4.a, design/wiki-web.md §2 `/graph?focus=…`).
import { useEffect, useRef, useState } from "react";
import { getJson } from "../api";
import { coverageText, EVIDENCE_COLORS, EDGE_FALLBACK_COLOR, LOW_CONFIDENCE, legendKinds, toElements,
         type Neighborhood } from "../graphModel";
import { docHref, graphHref, impactHref, navigate } from "../router";
import { useAnswer } from "../useAnswer";
import { loadCytoscape } from "../vendors";
import { Link } from "./Link";
import { Empty, ErrorState, Loading, NotIndexed } from "./States";

const LIMIT = 200;
const DENSE = 30;          // above this many nodes, labels show only when zoomed in (click a node for its name)

interface PathData { paths: number[][]; nodes: Record<string, { id: number; label: string; type: string }> }

function PathFinder({ from }: { from: string }) {
  const [to, setTo] = useState("");
  const [result, setResult] = useState<{ data?: PathData; error?: string; status?: string } | null>(null);
  const run = (e: React.FormEvent) => {
    e.preventDefault();
    if (!to.trim()) return;
    setResult(null);
    getJson<PathData>(`/graph/path?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to.trim())}&max_depth=6`)
      .then((a) => setResult({ data: a.data, status: a.status }), (err: Error) => setResult({ error: err.message }));
  };
  return (
    <section className="pathfinder">
      <h3>Path to…</h3>
      <form onSubmit={run}>
        <input value={to} onChange={(e) => setTo(e.target.value)} placeholder="qualified name, e.g. com.x.OrderRepository.save"
               aria-label="Path target" />
        <button type="submit">Find</button>
      </form>
      {result?.error && <ErrorState error={result.error} />}
      {result?.status === "not_found" && <Empty>Not in the graph: {(result.data as unknown as { missing?: string[] })?.missing?.join(", ")}</Empty>}
      {result?.data && result.status === "ok" && (result.data.paths.length ? (
        <ol>{result.data.paths.map((p, i) => (
          <li key={i}>{p.map((id, j) => {
            const n = result.data!.nodes[String(id)];
            return <span key={j}>{j > 0 && " → "}<Link href={graphHref(n?.label ?? String(id))}>{n?.label ?? id}</Link></span>;
          })}</li>))}</ol>
      ) : <Empty>No call path within 6 steps.</Empty>)}
    </section>
  );
}

export function GraphView({ focus, depth }: { focus: string; depth: number }) {
  const path = focus ? `/graph/neighborhood?node=${encodeURIComponent(focus)}&depth=${depth}&limit=${LIMIT}` : null;
  const loaded = useAnswer<Neighborhood>(path);
  const box = useRef<HTMLDivElement>(null);
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    if (loaded.state !== "done" || loaded.answer.status !== "ok" || !box.current) return;
    let cy: { destroy(): void } | null = null;
    let alive = true;
    const data = loaded.answer.data;
    loadCytoscape().then((cytoscape) => {
      if (!alive || !box.current) return;
      const dark = window.matchMedia?.("(prefers-color-scheme: dark)").matches;
      const dense = data.nodes.length > DENSE;
      const fg = dark ? "#e6e8ec" : "#1d2430";
      const inst = cytoscape({
        container: box.current,
        elements: toElements(data) as never,
        layout: { name: "cose", animate: false, nodeRepulsion: () => 9000, idealEdgeLength: () => 90 } as never,
        wheelSensitivity: 0.3,
        style: [
          { selector: "node", style: { label: "data(label)", "font-size": 10, color: fg, "background-color": "#9aa6b8",
                                       width: 14, height: 14, "text-valign": "bottom", "text-margin-y": 4,
                                       // dense neighborhoods: labels appear as you zoom in, never as a pile-up
                                       "min-zoomed-font-size": dense ? 14 : 0 } },
          { selector: "node.focus", style: { "background-color": "#e0552b", width: 22, height: 22, "font-weight": "bold",
                                             "min-zoomed-font-size": 0 } },
          { selector: "node.t-endpoint, node.t-batch_job", style: { shape: "diamond", "background-color": "#3b6fd8" } },
          { selector: "node.t-sql_table, node.t-db_model", style: { shape: "round-rectangle", "background-color": "#1f9d6b" } },
          { selector: "node.t-external_system", style: { shape: "hexagon", "background-color": "#d07a1f" } },
          { selector: "edge", style: { width: 1.5, "line-color": "data(color)", "target-arrow-color": "data(color)",
                                       "target-arrow-shape": "triangle", "curve-style": "bezier", "arrow-scale": 0.8 } },
          { selector: "edge.low", style: { "line-style": "dashed", opacity: 0.45 } },
        ] as never,
      });
      inst.on("tap", "node", (evt: { target: { data: (k: string) => string } }) => setSelected(evt.target.data("full")));
      inst.on("dbltap", "node", (evt: { target: { data: (k: string) => string } }) =>
        navigate(graphHref(evt.target.data("full"), depth)));
      cy = inst;
    });
    return () => { alive = false; cy?.destroy(); };
  }, [loaded, depth]);

  if (!focus) return <Empty>Pick a symbol: open a symbol page and choose “Explore graph”, or add <code>?focus=</code>.</Empty>;
  if (loaded.state === "loading") return <Loading what="Loading neighborhood" />;
  if (loaded.state === "error") return <ErrorState error={loaded.error} />;
  const answer = loaded.answer;
  if (answer.status === "not_indexed") return <NotIndexed />;
  if (answer.status !== "ok") {
    return <Empty>{answer.status === "not_found" ? <>No symbol <code>{focus}</code> in the graph.</>
                                                  : "The graph is not available here (it needs `discover serve`)."}</Empty>;
  }
  const data = answer.data;
  const total = Number(answer.totals.nodes ?? data.nodes.length);
  const focusNode = data.nodes.find((n) => n.id === data.focus);
  const kinds = legendKinds(data);
  return (
    <div className="graph-view">
      <header className="page-header">
        <p className="page-type">graph</p>
        <h1><code>{focusNode?.label ?? focus}</code></h1>
        <p className="graph-meta">
          {coverageText(data.nodes.length, total)} · {coverageText(data.edges.length, Number(answer.totals.edges ?? data.edges.length), "edges")}
          {" · depth "}
          {[1, 2].map((d) => d === depth ? <strong key={d}> {d}</strong>
                                        : <Link key={d} href={graphHref(focus, d)}> {d}</Link>)}
          {" · "}<Link href={impactHref(focusNode?.label ?? focus)}>impact</Link>
          {" · "}<Link href={docHref(`symbol:${focusNode?.label ?? focus}`)}>page</Link>
        </p>
        {answer.truncated && <p className="banner warn">Capped at {LIMIT} nodes — nearest first, most connected first.</p>}
      </header>
      <div className="graph-canvas" ref={box} role="img" aria-label={`Call graph around ${focusNode?.label ?? focus}`} />
      <aside className="graph-side">
        <h3>Legend</h3>
        <ul className="legend">
          {kinds.map((k) => (
            <li key={k}><span className="swatch" style={{ background: EVIDENCE_COLORS[k] ?? EDGE_FALLBACK_COLOR }} /> {k}</li>
          ))}
          <li><span className="swatch dashed" /> confidence &lt; {LOW_CONFIDENCE} (a guess; flows never follow it)</li>
        </ul>
        <p className="muted">Click a node to select it, double-click to re-center.</p>
        {selected && (
          <p>Selected: <code>{selected}</code><br />
            <Link href={graphHref(selected, depth)}>re-center</Link> · <Link href={docHref(`symbol:${selected}`)}>page</Link>
            {" · "}<Link href={impactHref(selected)}>impact</Link></p>
        )}
        <PathFinder from={focusNode?.label ?? focus} />
      </aside>
    </div>
  );
}
