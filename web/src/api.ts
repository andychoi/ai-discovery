// Typed access to the API v1 Answer envelope (design/wiki-web.md §3).
export type AnswerStatus = "ok" | "not_found" | "not_indexed" | "unknown" | string;

export interface Meta {
  scan_id: number | null;
  indexed_commit?: string | null;
  head_commit?: string | null;
  behind_by?: number | null;
  dirty?: boolean;
  stale_pages?: number;
  degraded: string[];
  contract_version: string;
}

export interface Answer<T> {
  status: AnswerStatus;
  data: T;
  totals: Record<string, number>;
  truncated: boolean;
  omitted: string[];
  meta: Meta;
}

export interface StatusData {
  project: { slug: string; repo: string | null; state_dir: string };
  scans: { scan_id: number; commit: string | null; status: string }[];
  pages: number;
}

export function isAnswer(value: unknown): value is Answer<unknown> {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return typeof v.status === "string" && "data" in v && "meta" in v && "totals" in v;
}

export async function getJson<T>(path: string, fetcher: typeof fetch = fetch): Promise<Answer<T>> {
  const res = await fetcher(`/api/v1${path}`, { headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} for ${path}`);
  const body: unknown = await res.json();
  if (!isAnswer(body)) throw new Error(`unexpected response shape for ${path}`);
  return body as Answer<T>;
}

/** The server refused a write: remote mode needs the bearer token printed by `discover serve`. */
export class AuthRequired extends Error {
  constructor() {
    super("this server requires a token for writes (printed by `discover serve --allow-remote`)");
    this.name = "AuthRequired";
  }
}

export async function postJson<T>(path: string, body: unknown, token: string | null = null,
                                  fetcher: typeof fetch = fetch): Promise<Answer<T>> {
  const headers: Record<string, string> = { Accept: "application/json", "Content-Type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetcher(`/api/v1${path}`, { method: "POST", headers, body: JSON.stringify(body) });
  if (res.status === 401) throw new AuthRequired();
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const err = (await res.json()) as { detail?: unknown };
      if (typeof err.detail === "string") detail = err.detail;
    } catch { /* keep status text */ }
    throw new Error(detail);
  }
  const parsed: unknown = await res.json();
  if (!isAnswer(parsed)) throw new Error(`unexpected response shape for ${path}`);
  return parsed as Answer<T>;
}

/** Validate a note before sending: trimmed, non-empty, within the server's 20,000-char limit. */
export function noteError(body: string): string | null {
  const text = body.trim();
  if (!text) return "Write something first.";
  if (text.length > 20000) return "Notes are limited to 20,000 characters.";
  return null;
}
