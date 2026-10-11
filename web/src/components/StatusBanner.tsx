import type { Meta } from "../api";

/** Freshness banner from any Answer's meta (design §2 UI rules). */
export function StatusBanner({ meta }: { meta: Meta | null }) {
  if (!meta) return null;
  const notes: string[] = [];
  if (meta.behind_by) notes.push(`index is ${meta.behind_by} commit(s) behind HEAD`);
  if (meta.behind_by === null && meta.indexed_commit && meta.head_commit) notes.push("indexed commit is not in this branch's history");
  if (meta.dirty) notes.push("working tree has uncommitted changes");
  if (meta.stale_pages) notes.push(`${meta.stale_pages} page(s) stale since the scan`);
  if (meta.degraded?.length) notes.push(`degraded: ${meta.degraded.join(", ")}`);
  if (!notes.length) return null;
  return (
    <p className="banner warn" role="status">
      {notes.join(" · ")} — run <code>discover scan</code> to refresh.
    </p>
  );
}
