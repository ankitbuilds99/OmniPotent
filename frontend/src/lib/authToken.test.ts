import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  authHeaders,
  clearAuthToken,
  getAuthToken,
  isAuthenticated,
  setApiKey,
  setSessionToken,
  wsProtocols,
} from "./authToken";

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

describe("getAuthToken", () => {
  it("returns empty when nothing is stored", () => {
    expect(getAuthToken()).toBe("");
    expect(isAuthenticated()).toBe(false);
  });

  it("prefers a login session over a pasted API key", () => {
    setApiKey("dcp_key");
    expect(getAuthToken()).toBe("dcp_key");

    setSessionToken("jwt");
    expect(getAuthToken()).toBe("jwt");
  });

  it("falls back to the build-time token only when storage is empty", async () => {
    vi.stubEnv("VITE_API_TOKEN", "baked-in");
    vi.resetModules();
    const mod = await import("./authToken");

    expect(mod.getAuthToken()).toBe("baked-in");

    mod.setApiKey("dcp_key");
    expect(mod.getAuthToken()).toBe("dcp_key");
  });

  it("survives localStorage throwing, as it does in private-mode Safari", () => {
    const getItem = vi
      .spyOn(Storage.prototype, "getItem")
      .mockImplementation(() => {
        throw new Error("SecurityError");
      });

    expect(getAuthToken()).toBe("");
    getItem.mockRestore();
  });
});

describe("clearAuthToken", () => {
  it("drops both credentials, not just the one that logged us in", () => {
    setSessionToken("jwt");
    setApiKey("dcp_key");

    clearAuthToken();

    expect(getAuthToken()).toBe("");
    expect(localStorage.getItem("session_token")).toBeNull();
    expect(localStorage.getItem("api_key")).toBeNull();
  });
});

describe("authHeaders", () => {
  it("omits Authorization when unauthenticated so an open dev backend still works", () => {
    expect(authHeaders({ "Content-Type": "application/json" })).toEqual({
      "Content-Type": "application/json",
    });
  });

  it("reads the token per call, so a login takes effect without a reload", () => {
    expect(authHeaders()).toEqual({});

    setSessionToken("jwt");

    expect(authHeaders({ "Content-Type": "application/json" })).toEqual({
      "Content-Type": "application/json",
      Authorization: "Bearer jwt",
    });
  });
});

describe("wsProtocols", () => {
  it("carries the token as a subprotocol, since browsers cannot set headers on a WebSocket", () => {
    expect(wsProtocols()).toEqual(["copilot-v1"]);

    setSessionToken("jwt");

    expect(wsProtocols()).toEqual(["copilot-v1", "auth-jwt"]);
  });
});
