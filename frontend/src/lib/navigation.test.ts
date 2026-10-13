import { describe, expect, it } from "vitest";

import { isPublicRoute } from "./navigation";

describe("route access policy", () => {
  it("allows public marketing and authentication routes", () => {
    expect(isPublicRoute("/marketing")).toBe(true);
    expect(isPublicRoute("/login/reset")).toBe(true);
    expect(isPublicRoute("/auth/callback")).toBe(true);
    expect(isPublicRoute("/invite/abc")).toBe(true);
  });

  it("keeps customer workspace routes private", () => {
    expect(isPublicRoute("/console")).toBe(false);
    expect(isPublicRoute("/dashboard")).toBe(false);
    expect(isPublicRoute("/settings/billing")).toBe(false);
    expect(isPublicRoute("/incidents")).toBe(false);
  });
});
