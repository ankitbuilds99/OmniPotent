// With VITE_API_URL unset — what the Vite dev proxy and the single-origin
// production deploy both assume — every client must call same-origin. The
// per-module `?? "http://localhost:8000"` defaults meant Console/chat, Billing
// and the WebSocket pointed a production browser at the *user's own machine*.

import { beforeEach, describe, expect, it, vi } from "vitest";

import { getMetrics } from "./api";
import { getUsage } from "./api/billing";
import { API_BASE, apiUrl, wsUrl } from "./lib/apiClient";

function mockFetch() {
  const spy = vi.fn().mockResolvedValue(
    new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } })
  );
  vi.stubGlobal("fetch", spy);
  return spy;
}

describe("API base URL", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it("is same-origin when VITE_API_URL is unset", () => {
    expect(import.meta.env.VITE_API_URL).toBeUndefined();
    expect(API_BASE).toBe("");
    expect(apiUrl("/api/incidents")).toBe("/api/incidents");
  });

  it("api.ts issues same-origin requests, not localhost:8000", async () => {
    const spy = mockFetch();
    await getMetrics();
    const url = String(spy.mock.calls[0][0]);
    expect(url).toBe("/metrics");
    expect(url).not.toContain("localhost:8000");
  });

  it("billing client issues same-origin requests, not localhost:8000", async () => {
    const spy = mockFetch();
    await getUsage();
    const url = String(spy.mock.calls[0][0]);
    expect(url.startsWith("/")).toBe(true);
    expect(url).not.toContain("localhost:8000");
  });

  it("websocket URL follows the page origin instead of localhost:8000", () => {
    const url = wsUrl("/ws/chat/t1");
    expect(url).toBe(`ws://${window.location.host}/ws/chat/t1`);
    expect(url).not.toContain("localhost:8000");
  });
});
