import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const API = process.env.CHESSBOOK_API ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  root: "frontend",
  resolve: {
    alias: { "@": new URL("./frontend/src", import.meta.url).pathname },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: API, changeOrigin: true },
    },
  },
  build: { outDir: "../dist", emptyOutDir: true, sourcemap: true },
});
