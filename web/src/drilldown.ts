// Drilldown across the business levels of the wiki (M4.4.b, ADR-0007):
// screen → flow → entity (state change) → table. The builders link pages in that direction
// (`routes_to`, `writes`, `see_also`); a page's links and backlinks together give both directions.
// No process diagram is drawn: every step of the chain is a stored, cited fact.
import { shortLabel } from "./graphModel";

export const LEVELS = [
  { type: "screen", label: "Screens" },
  { type: "flow", label: "Flows" },
  { type: "entity", label: "Business state" },
  { type: "table", label: "Tables" },
] as const;

export interface DrillItem { id: string; kind: string }
export interface DrillGroup { type: string; label: string; direction: "down" | "up"; items: DrillItem[] }
interface PageLinks {
  type: string;
  links: { dst: string; kind: string }[];
  backlinks: { src: string; kind: string }[];
}

const levelOf = (type: string) => LEVELS.findIndex((l) => l.type === type);
const typeOf = (id: string) => id.slice(0, Math.max(0, id.indexOf(":")));

/** Linked business-level pages grouped by level: deeper levels first ("down"), then shallower ("up"). */
export function drilldown(page: PageLinks): DrillGroup[] {
  const here = levelOf(page.type);
  const seen = new Map<string, DrillItem>();
  for (const l of page.links) if (!seen.has(l.dst)) seen.set(l.dst, { id: l.dst, kind: l.kind });
  for (const b of page.backlinks) if (!seen.has(b.src)) seen.set(b.src, { id: b.src, kind: b.kind });
  const groups: DrillGroup[] = [];
  LEVELS.forEach((level, i) => {
    if (level.type === page.type) return;
    const items = [...seen.values()].filter((it) => typeOf(it.id) === level.type)
      .sort((a, b) => a.id.localeCompare(b.id));
    if (items.length) groups.push({ ...level, direction: here < 0 || i > here ? "down" : "up", items });
  });
  return [...groups.filter((g) => g.direction === "down"), ...groups.filter((g) => g.direction === "up")];
}

/** Short label for a linked page id: a flow keeps `Class.method`, an entity its class name. */
export function drillLabel(id: string): string {
  const key = id.slice(id.indexOf(":") + 1);
  if (typeOf(id) === "flow") return shortLabel(key);
  if (typeOf(id) === "entity") return key.split(".").pop() || key;
  return key;
}
