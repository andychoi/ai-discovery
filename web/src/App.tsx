import { useEffect, useState } from "react";
import { BookOpen, AlertTriangle } from "lucide-react";
import { getJson, type Answer, type StatusData } from "./api";

// Scaffold shell (M4.2). Navigation, page reader and search arrive with M4.3.
export function App() {
  const [status, setStatus] = useState<Answer<StatusData> | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getJson<StatusData>("/status").then(setStatus, (e: Error) => setError(e.message));
  }, []);

  return (
    <main className="shell">
      <header>
        <BookOpen size={20} aria-hidden /> <h1>ai-discovery wiki</h1>
      </header>
      {error && (
        <p role="alert" className="error">
          <AlertTriangle size={16} aria-hidden /> {error}
        </p>
      )}
      {!error && !status && <p aria-busy="true">Loading…</p>}
      {status && status.status === "not_indexed" && <p>No index yet — run <code>discover scan</code>.</p>}
      {status && status.status === "ok" && (
        <section>
          <p>
            <strong>{status.data.project.slug}</strong> — {status.data.pages} pages, commit{" "}
            <code>{(status.meta.indexed_commit ?? "").slice(0, 10)}</code>
            {status.meta.behind_by ? ` (${status.meta.behind_by} commits behind HEAD)` : ""}
          </p>
        </section>
      )}
    </main>
  );
}
