import { useEffect, useState } from "react";
import { getJson, type Answer } from "./api";

export type Loadable<T> =
  | { state: "loading" }
  | { state: "error"; error: string }
  | { state: "done"; answer: Answer<T> };

/** Fetch an API v1 path; re-fetches when the path changes. */
export function useAnswer<T>(path: string | null): Loadable<T> {
  const [value, setValue] = useState<Loadable<T>>({ state: "loading" });
  useEffect(() => {
    if (path === null) return;
    let alive = true;
    setValue({ state: "loading" });
    getJson<T>(path).then(
      (answer) => alive && setValue({ state: "done", answer }),
      (e: Error) => alive && setValue({ state: "error", error: e.message }),
    );
    return () => {
      alive = false;
    };
  }, [path]);
  return value;
}
