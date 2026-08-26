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

const BACKEND = process.env.VITE_DEV_BACKEND || "http://localhost:8000";

const proxy: Record<string, { target: string; changeOrigin: boolean; ws: boolean }> = {};
API_PATHS.forEach((path) => {
  // ws:true so the /ws copilot socket and SSE streams survive the hop.
  proxy[path] = { target: BACKEND, changeOrigin: true, ws: true };
});

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy,
  },
});
