// Static export (roadmap item M3.10, design/wiki-web.md §6): `discover export --format html` inlines every
// answer the wiki reads as `window.__DISCOVERY_DATA__` (data.js). No fetch() — file:// blocks it.
import type { Answer } from "./api";

export interface SearchEntry { page_id: string; title: string; type: string; text: string }

export interface StaticData {
  version: number;
  answers: Record<string, Answer<unknown>>;
  search: SearchEntry[];
}

declare global {
  interface Window { __DISCOVERY_DATA__?: StaticData }
}

export function staticData(): StaticData | null {
  return typeof window !== "undefined" && window.__DISCOVERY_DATA__ ? window.__DISCOVERY_DATA__ : null;
}

function envelope<T>(status: string, data: T, base?: Answer<unknown>): Answer<T> {
  const meta = base?.meta ?? { scan_id: null, degraded: [], contract_version: "1" };
  return { status, data, totals: {}, truncated: false, omitted: [], meta };
}

/** Client-side search: every query token must occur in the title or text; title hits rank first. */
export function searchStatic(data: StaticData, q: string, limit: number): Answer<{ hits: unknown[] }> {
  const tokens = (q.toLowerCase().match(/[\p{L}\p{N}_]+/gu) ?? []);
  const hits: { page_id: string; title: string; type: string; snippet: string; score: number; source: string }[] = [];
  if (tokens.length) {
    for (const e of data.search) {
      const title = e.title.toLowerCase();
      const text = e.text.toLowerCase();
      if (!tokens.every((t) => title.includes(t) || text.includes(t))) continue;
      let score = title === q.trim().toLowerCase() ? 100 : 0;
      for (const t of tokens) score += (title.includes(t) ? 10 : 0) + (text.includes(t) ? 1 : 0);
      const at = text.indexOf(tokens[0] ?? "");
      const snippet = at < 0 ? "" : e.text.slice(Math.max(0, at - 40), at + 80).replace(/\s+/g, " ");
      hits.push({ page_id: e.page_id, title: e.title, type: e.type, snippet, score, source: "static" });
    }
  }
  hits.sort((a, b) => b.score - a.score || a.title.localeCompare(b.title));
  const top = hits.slice(0, limit);
  return { ...envelope("ok", { hits: top }, data.answers["/status"]), totals: { hits: top.length },
           truncated: hits.length > limit };
}

/** The recorded answer for a GET path, the client-side search, or a not_found envelope. */
export function staticAnswer<T>(data: StaticData, path: string): Answer<T> {
  const cut = path.indexOf("?");
  const pathname = cut < 0 ? path : path.slice(0, cut);
  const query = cut < 0 ? "" : path.slice(cut + 1);
  if (pathname === "/search") {
    const params = new URLSearchParams(query);
    return searchStatic(data, params.get("q") ?? "", Number(params.get("limit") ?? 20)) as Answer<T>;
  }
  const hit = data.answers[path] ?? data.answers[pathname];
  if (hit) return hit as Answer<T>;
  if (pathname === "/source") {
    return envelope("unknown", { reason: "source code is not included in the static export" } as T,
                    data.answers["/status"]);
  }
  if (pathname.startsWith("/graph/") || pathname === "/impact") {   // live queries: `discover serve` only
    return envelope("unknown", { reason: "the graph explorer and impact need `discover serve`" } as T,
                    data.answers["/status"]);
  }
  return envelope("not_found", null as T, data.answers["/status"]);
}
