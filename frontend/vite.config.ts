import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Every top-level backend path. Proxying them makes dev same-origin, so the
// browser sends no preflight and CORS misconfiguration cannot break local work
// — and it means VITE_API_URL can stay empty in dev, matching production, where
// FastAPI serves the built SPA from its own origin.
const API_PATHS = [
  "/api",
  "/admin",
  "/approve",
  "/audit",
  "/auth",
  "/billing",
  "/cache",
  "/chat",
  "/config",
  "/customers",
  "/feedback",
  "/frontend-config",
  "/github",
  "/healthz",
  "/integrations",
  "/me",
  "/metrics",
  "/model",
  "/oncall",
  "/readyz",
  "/remediate",
  "/reset",
  "/scim",
  "/share",
  "/signup",
  "/sources",
  "/usage",
  "/waitlist",
  "/webhooks",
  "/ws",
];

// @types/node is not a dependency and this file is typechecked on its own
// (tsconfig.node.json), so declare the one global we need rather than pulling
// in the whole Node type surface.
declare const process: { env: Record<string, string | undefined> };

// The backend is started on IPv4 loopback. Using `localhost` can resolve to
// IPv6 first on macOS, leaving the Vite proxy unable to reach 127.0.0.1:8000.
const BACKEND = process.env.VITE_DEV_BACKEND || "http://127.0.0.1:8000";

const proxy: Record<string, { target: string; changeOrigin: boolean; ws: boolean }> = {};
API_PATHS.forEach((path) => {
  // ws:true so the /ws copilot socket and SSE streams survive the hop.
  proxy[path] = { target: BACKEND, changeOrigin: true, ws: true };
});

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes("node_modules")) return undefined;
          if (id.includes("/three/")) return "vendor-three-core";
          if (id.includes("@react-three")) return "vendor-three-react";
          if (id.includes("framer-motion") || id.includes("gsap") || id.includes("lenis")) return "vendor-motion";
          if (id.includes("react-router") || id.includes("react-dom") || id.includes("/react/")) return "vendor-react";
          if (id.includes("lucide-react") || id.includes("simple-icons")) return "vendor-icons";
          if (id.includes("supabase") || id.includes("firebase") || id.includes("emailjs")) return "vendor-integrations";
          return undefined;
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy,
  },
});
