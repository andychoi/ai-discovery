import { useState } from "react";
import { AuthRequired, noteError, postJson } from "../api";

const TOKEN_KEY = "ai-discovery.write-token";

export function savedToken(): string | null {
  try { return window.sessionStorage.getItem(TOKEN_KEY); } catch { return null; }
}

/** Add a human note to a page (POST /api/v1/pages/{id}/notes); local-only unless the server allows remote writes. */
export function NoteForm({ pageId, onAdded }: { pageId: string; onAdded: () => void }) {
  const [body, setBody] = useState("");
  const [author, setAuthor] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [needToken, setNeedToken] = useState(false);
  const [token, setToken] = useState(savedToken() ?? "");

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const invalid = noteError(body);
    if (invalid) { setError(invalid); return; }
    setBusy(true);
    setError(null);
    try {
      await postJson(`/pages/${encodeURIComponent(pageId)}/notes`,
                     { body: body.trim(), author: author.trim() || null }, token || null);
      if (token) { try { window.sessionStorage.setItem(TOKEN_KEY, token); } catch { /* private mode */ } }
      setBody("");
      setNeedToken(false);
      onAdded();
    } catch (err) {
      if (err instanceof AuthRequired) setNeedToken(true);
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="note-form" onSubmit={submit} aria-label="Add a note">
      <label className="sr-only" htmlFor="note-body">Note</label>
      <textarea id="note-body" rows={3} placeholder="Add a note for readers of this page…" value={body}
                onChange={(e) => setBody(e.target.value)} disabled={busy} />
      <div className="note-row">
        <input aria-label="Your name (optional)" placeholder="Your name (optional)" value={author}
               onChange={(e) => setAuthor(e.target.value)} disabled={busy} maxLength={200} />
        <button type="submit" disabled={busy || !body.trim()}>{busy ? "Saving…" : "Add note"}</button>
      </div>
      {needToken && (
        <input aria-label="Write token" placeholder="Write token from `discover serve`" type="password" value={token}
               onChange={(e) => setToken(e.target.value)} />
      )}
      {error && <p className="note-error" role="alert">{error}</p>}
      <p className="muted small">Notes are stored in this project's index and kept across rescans.</p>
    </form>
  );
}
