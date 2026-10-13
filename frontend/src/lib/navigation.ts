/** Shared browser-storage keys used by routing and invitation flows. */
export const PENDING_INVITATION_KEY = "pending_invitation";

const PUBLIC_ROUTE_PREFIXES = [
  "/marketing",
  "/login",
  "/auth/",
  "/pricing",
  "/capabilities",
  "/api-docs",
  "/invite/",
] as const;

/** Routes that may render before a customer session exists. */
export function isPublicRoute(pathname: string): boolean {
  return PUBLIC_ROUTE_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(prefix),
  );
}
