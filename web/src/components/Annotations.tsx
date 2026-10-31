import { useState } from "react";
import { AuthRequired, postJson } from "../api";
import { savedToken } from "./NoteForm";

export interface Annotation {
  id: number; kind: "finding" | "note" | "decision"; category: string | null; text: string;
  evidence: { path?: string; line?: number; note?: string }[]; status: string | null;
  signer_kind: "agent" | "human"; client: string | null; created_at: string; updated_by: string | null;
  flags?: { kind: string; line: number; excerpt: string }[];
}

/** Which status buttons a person gets for an annotation (pure, unit-tested). */
export function humanActions(a: Annotation): { label: string; status: string }[] {
  if (a.kind === "decision" && a.status === "proposed") return [{ label: "Accept", status: "accepted" }, { label: "Reject", status: "rejected" }];
  if (a.kind === "finding" && (a.status === "open" || a.status === "acknowledged"))
    return [{ label: "Resolve", status: "resolved" }, { label: "False positive", status: "false_positive" }];
  return [];
}

function AnnotationItem({ a, onChanged }: { a: Annotation; onChanged: () => void }) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const act = async (status: string) => {
    setBusy(true);
    setError(null);
    try {
      const ans = await postJson<{ status: string }>(`/annotations/${a.id}/status`, { status }, savedToken());
      if (ans.status !== "ok") setError((ans.data as unknown as { remedy?: string }).remedy ?? "not updated");
      else onChanged();
    } catch (err) {
      setError(err instanceof AuthRequired ? "Add a note first to enter the write token." : (err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const unconfirmed = a.kind === "decision" && a.status === "proposed";
  return (
    <li className={`annotation ${a.signer_kind}`}>
      <p className="badges">
        <span className={`badge ${a.signer_kind === "agent" ? "prose" : "ok"}`}>{a.signer_kind}</span>
        <span className="badge">{a.kind}{a.category ? ` · ${a.category}` : ""}</span>
        {a.status && <span className={`badge ${unconfirmed ? "warn" : ""}`}>{unconfirmed ? "proposed — unconfirmed" : a.status}</span>}
        {a.flags?.length ? <span className="badge warn" title={a.flags.map((f) => `${f.kind}: ${f.excerpt}`).join("\n")}>flagged</span> : null}
      </p>
      <p className="annotation-text">{a.text}</p>
      {a.evidence.length > 0 && (
        <ul className="evidence">{a.evidence.map((e, i) => (
          <li key={i}><code>{e.path ?? ""}{e.line ? `:${e.line}` : ""}</code> {e.note ?? ""}</li>))}</ul>
      )}
      <p className="muted small">{a.client ?? "unknown client"} · {a.created_at.slice(0, 10)}{a.updated_by && a.updated_by !== a.signer_kind ? ` · updated by ${a.updated_by}` : ""}</p>
      {humanActions(a).length > 0 && (
        <p className="annotation-actions">
          {humanActions(a).map((b) => <button key={b.status} disabled={busy} onClick={() => act(b.status)}>{b.label}</button>)}
        </p>
      )}
      {error && <p className="note-error" role="alert">{error}</p>}
    </li>
  );
}

export function Annotations({ items, onChanged }: { items: Annotation[]; onChanged: () => void }) {
  if (!items.length) return null;
  return (
    <>
      <h3>Agent &amp; reviewer annotations</h3>
      <p className="muted small">Not facts: signed findings, notes and proposed decisions. Decisions stay unconfirmed until a person accepts them.</p>
      <ul className="annotations">{items.map((a) => <AnnotationItem key={a.id} a={a} onChanged={onChanged} />)}</ul>
    </>
  );
}
