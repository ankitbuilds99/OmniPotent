// With VITE_API_URL unset — what the Vite dev proxy and the single-origin
// production deploy both assume — every client must call same-origin. The
// per-module `?? "http://localhost:8000"` defaults meant Console/chat, Billing
// and the WebSocket pointed a production browser at the *user's own machine*.

import { beforeEach, describe, expect, it, vi } from "vitest";

import * as config from "./config";
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

// ./config and ./lib/apiClient both once claimed to be the source of truth for
// the API base, the bearer token and the WebSocket URL. They are not two
// derivations of one thing — requests need a *relative* base so they follow
// whatever origin served the page, and links need an *absolute* one — but config
// also carried a `wsUrl` and an `authHeaders` that nothing imported, the second
// of which read a build-time token and so could not see a run-time sign-in. That
// is the exact bug lib/authToken was written to fix, sitting in the tree next to
// the fix, one import away from being used again.
describe("config and apiClient do not overlap", () => {
  it("splits by what the value is for: relative for requests, absolute for links", () => {
    expect(API_BASE).toBe("");
    expect(() => new URL(config.API_BASE_URL)).not.toThrow();
    expect(config.API_BASE_URL).not.toBe("");
  });

  it("keeps the credential and the socket URL out of config entirely", () => {
    expect(Object.keys(config).sort()).toEqual(["API_BASE_URL"]);
  });
});
