import { useAnswer } from "../useAnswer";
import { docHref } from "../router";
import { snippetHtml, type Hit } from "../search";
import { Link } from "./Link";
import { Empty, ErrorState, Loading, NotIndexed, Showing } from "./States";

export function SearchResults({ q }: { q: string }) {
  const res = useAnswer<{ hits: Hit[] }>(q.trim() ? `/search?q=${encodeURIComponent(q)}&limit=50` : null);
  if (!q.trim()) return <Empty>Type a query (⌘K).</Empty>;
  if (res.state === "loading") return <Loading what="Searching" />;
  if (res.state === "error") return <ErrorState error={res.error} />;
  if (res.answer.status === "not_indexed") return <NotIndexed />;
  const hits = res.answer.data.hits;
  return (
    <section>
      <h1>Search: {q}</h1>
      <Showing shown={hits.length} total={res.answer.totals.hits ?? hits.length} />
      {hits.length ? (
        <ul className="results">
          {hits.map((h) => (
            <li key={h.page_id}>
              <Link href={docHref(h.page_id)}>{h.title}</Link> <span className="muted">{h.type}</span>
              {h.snippet && <p className="snippet" dangerouslySetInnerHTML={{ __html: snippetHtml(h.snippet) }} />}
            </li>
          ))}
        </ul>
      ) : <Empty>No matches.</Empty>}
    </section>
  );
}
