import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Static export bundle (roadmap item M3.10): opened from file://, where browsers refuse module scripts and
// any fetch of a lazily loaded chunk. One classic (IIFE) script with every dynamic import inlined, relative
// asset paths; `discover export --format html` adds data.js before it.
export default defineConfig({
  plugins: [react()],
  base: "./",
  build: {
    outDir: "../src/ai_discovery/server/static_export",
    emptyOutDir: true,
    assetsDir: "assets",
    sourcemap: false,
    chunkSizeWarningLimit: 16384,
    rollupOptions: {
      output: {
        format: "iife",
        inlineDynamicImports: true,
        entryFileNames: "assets/app.js",
        assetFileNames: "assets/[name][extname]",
      },
    },
  },
});
