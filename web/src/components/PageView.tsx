import { useEffect, useMemo, useRef, useState } from "react";
import { FileCode2, X } from "lucide-react";
import { useAnswer } from "../useAnswer";
import { markdownToHtml } from "../markdown";
import { docHref, navigate } from "../router";
import { loadMermaid } from "../vendors";
import { getJson } from "../api";
import { Link } from "./Link";
import { NoteForm } from "./NoteForm";
import { Empty, ErrorState, Loading, NotIndexed } from "./States";

interface Citation { path: string; line_start: number; line_end: number }
interface PageData {
  page_id: string; type: string; title: string; body_md: string;
  links: { dst: string; kind: string }[]; backlinks: { src: string; kind: string }[];
  citations: Citation[]; confidence: number; freshness: string; stale_reason?: string | null;
  notes: { id: number; author: string | null; body: string; created_at: string }[];
  successor?: string;
}
interface SourceData { path: string; first_line: number; start: number; end: number; lines: string[] }

let purify: ((html: string) => string) | null = null;
async function sanitize(html: string): Promise<string> {
  if (!purify) {
    const mod = await import("dompurify");
    purify = (h) => mod.default.sanitize(h, { ADD_ATTR: ["target", "data-internal"] });
  }
  return purify(html);
}

function confidenceBadge(c: number) {
  if (c >= 0.99) return <span className="badge ok" title="Derived from parsed code">structure</span>;
  if (c >= 0.5) return <span className="badge prose" title="Narrative written by a model">model-written · {c.toFixed(2)}</span>;
  return <span className="badge warn" title="Generation failed; structural stub">stub · {c.toFixed(2)}</span>;
}

function SourcePanel({ cite, onClose }: { cite: Citation; onClose: () => void }) {
  const [state, setState] = useState<{ data?: SourceData; error?: string }>({});
  useEffect(() => {
    getJson<SourceData>(`/source?path=${encodeURIComponent(cite.path)}&start=${cite.line_start}&end=${cite.line_end}`)
      .then((a) => setState(a.status === "ok" ? { data: a.data } : { error: "source not available" }),
            (e: Error) => setState({ error: e.message }));
  }, [cite]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <aside className="source-panel" aria-label="Source">
      <header>
        <FileCode2 size={14} aria-hidden /> <code>{cite.path}:{cite.line_start}</code>
        <button onClick={onClose} aria-label="Close source"><X size={14} /></button>
      </header>
      {state.error && <ErrorState error={state.error} />}
      {!state.error && !state.data && <Loading what="Loading source" />}
      {state.data && (
        <pre className="source">
          {state.data.lines.map((line, i) => {
            const n = state.data!.first_line + i;
            const hit = n >= state.data!.start && n <= state.data!.end;
            return (
              <div key={n} className={hit ? "hit" : undefined}>
                <span className="ln">{n}</span>{line || " "}
              </div>
            );
          })}
        </pre>
      )}
    </aside>
  );
}

export function PageView({ pageId }: { pageId: string }) {
  const [version, setVersion] = useState(0);
  const loaded = useAnswer<PageData>(`/pages/${encodeURIComponent(pageId)}`, version);
  const [html, setHtml] = useState<string>("");
  const [cite, setCite] = useState<Citation | null>(null);
  const body = useRef<HTMLDivElement>(null);

  const page = loaded.state === "done" && loaded.answer.status === "ok" ? loaded.answer.data : null;
  const raw = useMemo(() => (page ? markdownToHtml(page.body_md) : ""), [page]);

  useEffect(() => {
    let alive = true;
    setCite(null);
    sanitize(raw).then((h) => alive && setHtml(h));
    return () => { alive = false; };
  }, [raw]);

  useEffect(() => {
    const el = body.current;
    if (!el || !html) return;
    const blocks = Array.from(el.querySelectorAll<HTMLElement>("pre.mermaid-src"));
    if (!blocks.length) return;
    loadMermaid().then((mermaid) => {
      const dark = window.matchMedia?.("(prefers-color-scheme: dark)").matches;
      mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: dark ? "dark" : "default" });
      blocks.forEach((b) => b.classList.replace("mermaid-src", "mermaid"));
      return mermaid.run({ nodes: blocks, suppressErrors: true });
    }).catch(() => undefined);
  }, [html]);

  const onBodyClick = (e: React.MouseEvent) => {
    const a = (e.target as HTMLElement).closest("a[data-internal]") as HTMLAnchorElement | null;
    if (a && !e.metaKey && !e.ctrlKey) {
      e.preventDefault();
      navigate(a.getAttribute("href")!);
    }
  };

  if (loaded.state === "loading") return <Loading what="Loading page" />;
  if (loaded.state === "error") return <ErrorState error={loaded.error} />;
  const answer = loaded.answer;
  if (answer.status === "not_indexed") return <NotIndexed />;
  if (answer.status === "not_found") {
    const successor = (answer.data as Partial<PageData>).successor;
    return (
      <div className="state">
        <h2>Page not found</h2>
        <p><code>{pageId}</code> is not in the current scan.</p>
        {successor && <p>It was removed or renamed — see <Link href={docHref(successor)}>{successor}</Link>.</p>}
      </div>
    );
  }
  const p = answer.data;
  return (
    <div className={cite ? "reader with-source" : "reader"}>
      <article>
        <header className="page-header">
          <p className="page-type">{p.type}</p>
          <h1>{p.title}</h1>
          <p className="badges">
            {confidenceBadge(p.confidence)}
            {p.freshness === "stale"
              ? <span className="badge warn" title={p.stale_reason ?? ""}>stale</span>
              : <span className="badge ok">fresh</span>}
          </p>
          {p.freshness === "stale" && (
            <p className="banner warn" role="status">
              The code behind this page changed since the scan ({p.stale_reason}). Run <code>discover scan</code> to refresh it.
            </p>
          )}
          {p.confidence < 0.5 && <p className="banner warn">This page is a stub: generation failed, only structure is shown.</p>}
        </header>
        <div className="markdown" ref={body} onClick={onBodyClick} dangerouslySetInnerHTML={{ __html: html }} />
      </article>
      <aside className="rail">
        <h3>Citations</h3>
        {p.citations.length ? (
          <ul>{p.citations.map((c) => (
            <li key={`${c.path}:${c.line_start}`}>
              <button className="linklike" onClick={() => setCite(c)}><code>{c.path}:{c.line_start}{c.line_end !== c.line_start ? `-${c.line_end}` : ""}</code></button>
            </li>))}</ul>
        ) : <Empty>No citations.</Empty>}
        <h3>Backlinks</h3>
        {p.backlinks.length ? (
          <ul>{p.backlinks.map((b) => <li key={`${b.src}${b.kind}`}><Link href={docHref(b.src)}>{b.src}</Link> <span className="muted">{b.kind}</span></li>)}</ul>
        ) : <Empty>Nothing links here.</Empty>}
        <h3>Notes</h3>
        {p.notes.length ? (
          <ul className="notes">{p.notes.map((n) => <li key={n.id}><p>{n.body}</p><p className="muted">{n.author ?? "anonymous"} · {n.created_at.slice(0, 10)}</p></li>)}</ul>
        ) : <Empty>No notes yet.</Empty>}
        <NoteForm pageId={p.page_id} onAdded={() => setVersion((v) => v + 1)} />
      </aside>
      {cite && <SourcePanel cite={cite} onClose={() => setCite(null)} />}
    </div>
  );
}
