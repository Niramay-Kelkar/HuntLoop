/**
 * Demo mode for the frontend, controlled by NEXT_PUBLIC_DEMO_MODE - a
 * build time env var (same mechanism as NEXT_PUBLIC_API_URL, see
 * lib/api.ts), since this value decides which components render at
 * all and must be known before the client bundle is built, not read
 * at runtime.
 *
 * This is independent of the backend's own DEMO_MODE (huntloop.demo_mode)
 * and must be set to match it when both are deployed together - the
 * frontend flag only controls what the UI shows (banner, hidden
 * controls, messaging); it never bypasses anything the backend itself
 * enforces.
 */
export function isDemoMode(): boolean {
  return process.env.NEXT_PUBLIC_DEMO_MODE === "true";
}
