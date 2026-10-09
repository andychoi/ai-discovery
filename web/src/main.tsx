import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource/inter/400.css";
import "@fontsource/inter/600.css";
import "./styles.css";
import { App } from "./App";
import * as renderers from "./vendors";

// Keep the on-demand renderer chunks in the build (feature views call these in
// M4.3/M4.4); exposed for diagnostics, nothing is loaded until used.
(window as unknown as { __discoveryRenderers: typeof renderers }).__discoveryRenderers = renderers;

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
