import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The bundle is written straight into the Python package, which is what lets one
// container serve the interface and the API from one process on one port. Nothing
// is fetched from a CDN at runtime: the room this is defended in may have no route
// to the internet, and a map that fails to load there is a map that does not exist.
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
