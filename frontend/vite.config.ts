import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// During development the API runs on :8786; Vite proxies /api to it.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://127.0.0.1:8786", changeOrigin: false } },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    chunkSizeWarningLimit: 1500,
  },
});
