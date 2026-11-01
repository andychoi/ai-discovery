// Impact view (roadmap item M4.4.a; data = MCP get_impact, M5.2).
import { useState } from "react";
import { impactMarkdown, type ImpactData } from "../graphModel";
import { docHref, graphHref, impactHref, navigate } from "../router";
import { useAnswer } from "../useAnswer";
import { Link } from "./Link";
import { Empty, ErrorState, Loading, NotIndexed } from "./States";

export function ImpactView({ target }: { target: string }) {
  const [draft, setDraft] = useState(target);
  const [copied, setCopied] = useState(false);
  const loaded = useAnswer<ImpactData>(target ? `/impact?target=${encodeURIComponent(target)}` : null);
  const form = (
    <form className="impact-form" onSubmit={(e) => { e.preventDefault(); if (draft.trim()) navigate(impactHref(draft.trim())); }}>
      <input value={draft} onChange={(e) => setDraft(e.target.value)} aria-label="Impact target"
             placeholder="symbol, file:…, table:…, screen:…, entity:…, endpoint:…" />
      <button type="submit">Analyse</button>
    </form>
  );
  if (!target) return <div className="impact-view"><h1>Impact</h1>{form}</div>;
  if (loaded.state === "loading") return <Loading what="Computing impact" />;
  if (loaded.state === "error") return <ErrorState error={loaded.error} />;
  const answer = loaded.answer;
  if (answer.status === "not_indexed") return <NotIndexed />;
  if (answer.status !== "ok") {
    return (
      <div className="impact-view"><h1>Impact</h1>{form}
        <Empty>{answer.status === "ambiguous" ? <>“{target}” matches several things — be more specific.</>
                : answer.status === "not_found" ? <>Nothing called <code>{target}</code> in this scan.</>
                : "Impact is not available here (it needs `discover serve`)."}</Empty>
      </div>
    );
  }
  const d = answer.data;
  const copy = () => {
    navigator.clipboard?.writeText(impactMarkdown(d, answer.totals)).then(() => setCopied(true), () => setCopied(false));
  };
  return (
    <div className="impact-view">
      <header className="page-header">
        <p className="page-type">impact</p>
        <h1><code>{d.target}</code></h1>
        <p className="graph-meta">
          depth {d.depth} · {d.items.length} item(s){answer.truncated ? " (capped)" : ""} ·{" "}
          <button className="linklike" onClick={copy}>{copied ? "copied ✓" : "copy as IMPACT_REPORT.md"}</button>
        </p>
        {d.note && <p className="banner warn">{d.note}</p>}
      </header>
      {form}
      <h2>What may break</h2>
      {d.items.length ? (
        <div className="table-scroll"><table className="impact">
          <thead><tr><th>#</th><th>Item</th><th>Kind</th><th>Score</th><th>Depth</th><th>Certainty</th><th>Via</th></tr></thead>
          <tbody>{d.items.map((it, i) => (
            <tr key={`${it.node}-${i}`} className={it.certainty === "possible" ? "faint" : undefined}>
              <td>{i + 1}</td>
              <td>{it.page_id ? <Link href={docHref(it.page_id)}>{it.node}</Link> : <code>{it.node}</code>}
                {" "}<Link href={graphHref(it.node)} className="muted">graph</Link></td>
              <td>{it.kind}</td><td>{it.score.toFixed(2)}</td><td>{it.depth}</td><td>{it.certainty ?? ""}</td>
              <td className="muted">{it.via.map((v) => `${v.origin} ${v.confidence.toFixed(2)}`).join(" · ")}</td>
            </tr>))}</tbody>
        </table></div>
      ) : <Empty>Nothing in the graph depends on this.</Empty>}
      <h2>Entry points reached</h2>
      {d.entry_points_reached.length
        ? <ul>{d.entry_points_reached.map((e) => <li key={e}>{e.includes(":") ? <Link href={docHref(e)}>{e}</Link> : <code>{e}</code>}</li>)}</ul>
        : <Empty>None.</Empty>}
      <h2>Unknowns</h2>
      {d.unknowns.length
        ? <ul>{d.unknowns.map((u, i) => <li key={i}><code>{u.node ?? ""}</code> {u.reason} {u.call && <code>{u.call}</code>} {u.detail}</li>)}</ul>
        : <Empty>No unresolved calls on the paths — the list above is complete for the resolved graph.</Empty>}
    </div>
  );
}
