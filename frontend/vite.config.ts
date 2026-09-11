import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Сборка пишется прямо в пакет Python — именно это позволяет одному контейнеру
// отдавать интерфейс и API одним процессом на одном порту. Ничего не подтягивается с
// CDN во время работы: в помещении, где это будут защищать, может не быть выхода в
// интернет, а карта, которая там не загрузилась, — это карта, которой нет.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../src/cosmo_net/serving/static",
    emptyOutDir: true,
    chunkSizeWarningLimit: 900,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
});
