// Heavy renderers are loaded on demand (split chunks), and always from the
// bundle — never a CDN. Feature views (M4.3/M4.4) call these loaders. No BPMN/DMN viewer:
// process views are evidence-backed pages and decision tables are markdown (ADR-0007).
export const loadMermaid = () => import("mermaid").then((m) => m.default);
export const loadCytoscape = () => import("cytoscape").then((m) => m.default);
export const loadMarkdown = async () => {
  const [{ marked }, purify] = await Promise.all([import("marked"), import("dompurify")]);
  return (md: string) => purify.default.sanitize(marked.parse(md, { async: false }) as string);
};
