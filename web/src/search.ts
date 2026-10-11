// Search palette state (pure reducer, unit-tested).
export interface Hit {
  page_id: string;
  title: string;
  type: string;
  snippet: string;
  score: number;
  source: string;
}

export interface PaletteState {
  open: boolean;
  query: string;
  hits: Hit[];
  active: number;
}

export type PaletteAction =
  | { type: "open" }
  | { type: "close" }
  | { type: "query"; query: string }
  | { type: "results"; query: string; hits: Hit[] }
  | { type: "move"; delta: number };

export const initialPalette: PaletteState = { open: false, query: "", hits: [], active: 0 };

export function paletteReducer(state: PaletteState, action: PaletteAction): PaletteState {
  switch (action.type) {
    case "open":
      return { ...state, open: true };
    case "close":
      return { ...initialPalette };
    case "query":
      return { ...state, query: action.query, active: 0, hits: action.query.trim() ? state.hits : [] };
    case "results":
      // ignore late responses for an older query
      return action.query === state.query ? { ...state, hits: action.hits, active: 0 } : state;
    case "move": {
      if (!state.hits.length) return state;
      const n = state.hits.length;
      return { ...state, active: (state.active + action.delta + n) % n };
    }
  }
}

/** Render "[a]bc" snippet markers from the API as <mark> (escaped otherwise). */
export function snippetHtml(snippet: string): string {
  const esc = snippet.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return esc.replace(/\[([^\]]*)\]/g, "<mark>$1</mark>");
}
