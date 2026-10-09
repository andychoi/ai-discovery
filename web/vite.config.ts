import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The SPA ships inside the Python wheel (src/ai_discovery/server/static) and is
// served by `discover serve` on one port. Everything must be local: no CDN.
export default defineConfig({
  plugins: [react()],
  base: "/",
  build: {
    outDir: "../src/ai_discovery/server/static",
    emptyOutDir: true,
    assetsDir: "assets",
    sourcemap: false,
    chunkSizeWarningLimit: 4096,
  },
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:7341" },
  },
});
