import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/ws": { target: "http://127.0.0.1:8000", ws: true },
      "/ingest": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/analyze": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/approve": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/execute": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/logs": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/policy": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/health": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/post-mortem": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/post_mortems": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
});
