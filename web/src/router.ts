// Tiny History-API router (no dependency). The server falls back to
// index.html for every non-API path, so deep links work on reload.
export type Route =
  | { name: "overview" }
  | { name: "doc"; pageId: string }
  | { name: "search"; q: string }
  | { name: "unknown"; path: string };

export const PAGE_TYPES = [
  "overview", "domain", "module", "file", "symbol", "endpoint", "screen", "menu", "entity", "table",
  "stored_proc", "batch_job", "interface", "flow", "onboarding", "tcode", "dynpro",
] as const;

export function isPageId(value: string): boolean {
  const i = value.indexOf(":");
  return i > 0 && i < value.length - 1 && (PAGE_TYPES as readonly string[]).includes(value.slice(0, i));
}

export function docHref(pageId: string): string {
  return pageId === "overview:system" ? "/" : `/docs/${encodeURIComponent(pageId)}`;
}

export function searchHref(q: string): string {
  return `/search?q=${encodeURIComponent(q)}`;
}

export function parseRoute(rawPath: string, search = ""): Route {
  // tolerate "//x" and "/x/" (hand-typed or proxied URLs)
  const pathname = rawPath.replace(/\/{2,}/g, "/").replace(/(.)\/$/, "$1");
  if (pathname === "/" || pathname === "") return { name: "overview" };
  if (pathname.startsWith("/docs/")) {
    const pageId = decodeURIComponent(pathname.slice("/docs/".length));
    return isPageId(pageId) ? { name: "doc", pageId } : { name: "unknown", path: pathname };
  }
  if (pathname === "/search") return { name: "search", q: new URLSearchParams(search).get("q") ?? "" };
  return { name: "unknown", path: pathname };
}

export function navigate(href: string): void {
  if (href === window.location.pathname + window.location.search) return;
  window.history.pushState({}, "", href);
  window.dispatchEvent(new PopStateEvent("popstate"));
  window.scrollTo(0, 0);
}
