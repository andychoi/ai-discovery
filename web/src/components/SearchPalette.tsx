import { useEffect, useReducer, useRef } from "react";
import { Search } from "lucide-react";
import { getJson } from "../api";
import { docHref, navigate } from "../router";
import { initialPalette, paletteReducer, snippetHtml, type Hit } from "../search";

/** ⌘K / Ctrl+K palette over /api/v1/search. */
export function SearchPalette() {
  const [state, dispatch] = useReducer(paletteReducer, initialPalette);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        dispatch({ type: "open" });
      } else if (e.key === "/" && !(e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement)) {
        e.preventDefault();
        dispatch({ type: "open" });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (state.open) input.current?.focus();
  }, [state.open]);

  useEffect(() => {
    const q = state.query.trim();
    if (!q) return;
    const t = setTimeout(() => {
      getJson<{ hits: Hit[] }>(`/search?q=${encodeURIComponent(q)}&limit=20`).then(
        (a) => dispatch({ type: "results", query: state.query, hits: a.status === "ok" ? a.data.hits : [] }),
        () => dispatch({ type: "results", query: state.query, hits: [] }),
      );
    }, 150);
    return () => clearTimeout(t);
  }, [state.query]);

  const go = (hit: Hit | undefined) => {
    if (!hit) return;
    dispatch({ type: "close" });
    navigate(docHref(hit.page_id));
  };

  return (
    <>
      <button className="search-button" onClick={() => dispatch({ type: "open" })} aria-label="Search (Ctrl+K)">
        <Search size={14} aria-hidden /> Search <kbd>⌘K</kbd>
      </button>
      {state.open && (
        <div className="palette-backdrop" onClick={() => dispatch({ type: "close" })}>
          <div className="palette" role="dialog" aria-label="Search" onClick={(e) => e.stopPropagation()}>
            <input
              ref={input}
              value={state.query}
              placeholder="Search screens, tables, files, symbols…"
              onChange={(e) => dispatch({ type: "query", query: e.target.value })}
              onKeyDown={(e) => {
                if (e.key === "Escape") dispatch({ type: "close" });
                else if (e.key === "ArrowDown") { e.preventDefault(); dispatch({ type: "move", delta: 1 }); }
                else if (e.key === "ArrowUp") { e.preventDefault(); dispatch({ type: "move", delta: -1 }); }
                else if (e.key === "Enter") go(state.hits[state.active]);
              }}
              aria-label="Search query"
            />
            <ul role="listbox">
              {state.hits.map((h, i) => (
                <li key={h.page_id} role="option" aria-selected={i === state.active}
                    className={i === state.active ? "active" : undefined}
                    onMouseDown={(e) => { e.preventDefault(); go(h); }}>
                  <span className="hit-title">{h.title}</span> <span className="muted">{h.type}</span>
                  {h.snippet && <p className="snippet" dangerouslySetInnerHTML={{ __html: snippetHtml(h.snippet) }} />}
                </li>
              ))}
              {state.query.trim() && !state.hits.length && <li className="muted">No matches.</li>}
            </ul>
          </div>
        </div>
      )}
    </>
  );
}
