import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
// Every top-level backend path. Proxying them makes dev same-origin, so the
// browser sends no preflight and CORS misconfiguration cannot break local work
// — and it means VITE_API_URL can stay empty in dev, matching production, where
// FastAPI serves the built SPA from its own origin.
var API_PATHS = [
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
var BACKEND = process.env.VITE_DEV_BACKEND || "http://localhost:8000";
var proxy = {};
API_PATHS.forEach(function (path) {
    // ws:true so the /ws copilot socket and SSE streams survive the hop.
    proxy[path] = { target: BACKEND, changeOrigin: true, ws: true };
});
export default defineConfig({
    plugins: [react()],
    server: {
        port: 5173,
        proxy: proxy,
    },
});
