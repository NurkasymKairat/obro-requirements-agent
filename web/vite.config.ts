import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Режим разработки: фронт на :5173, запросы /api проксируются на API (python -m api, :8000).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": process.env.API_URL ?? "http://127.0.0.1:8000" },
  },
});
