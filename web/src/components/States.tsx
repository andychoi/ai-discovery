import { AlertTriangle, Inbox, Loader2 } from "lucide-react";
import type { ReactNode } from "react";

export function Loading({ what = "Loading" }: { what?: string }) {
  return (
    <p className="state" aria-busy="true">
      <Loader2 size={16} className="spin" aria-hidden /> {what}…
    </p>
  );
}

export function ErrorState({ error }: { error: string }) {
  return (
    <p className="state error" role="alert">
      <AlertTriangle size={16} aria-hidden /> Could not load: {error}
    </p>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <p className="state muted">
      <Inbox size={16} aria-hidden /> {children}
    </p>
  );
}

export function NotIndexed() {
  return (
    <div className="state">
      <h2>No index yet</h2>
      <p>
        Run <code>discover scan</code> in the repository, then reload this page.
      </p>
    </div>
  );
}

export function Showing({ shown, total }: { shown: number; total: number }) {
  return <p className="showing">showing {shown} of {total}</p>;
}
