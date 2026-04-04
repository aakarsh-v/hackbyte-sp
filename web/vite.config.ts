import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: Vite serves the React app with HMR (default port 5173; if busy, next free e.g. 5174).
// Prod-like: FastAPI on :8000 serves web/dist + API on one origin — see README "Development".
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: false,
    proxy: {
      "/ws": { target: "http://127.0.0.1:8000", ws: true },
      "/ingest": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/ingest/batch": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/analyze": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/incident-query": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/approve": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/execute": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/logs": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/policy": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/health": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/metrics": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/post-mortem": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/post_mortems": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
});
