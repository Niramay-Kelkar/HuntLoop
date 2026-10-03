/**
 * Next.js 16 renamed the `middleware.ts` file convention to `proxy.ts`
 * (same mechanism, same per-request hook before a route renders - see
 * node_modules/next/dist/docs/01-app/03-api-reference/03-file-conventions/proxy.md).
 * Clerk's clerkMiddleware() helper is unaffected by that rename - it's
 * just a plain (request, event) => response function, which is exactly
 * what both the old `middleware` export and the new `proxy` export
 * expect.
 *
 * Protects the /admin route tree: an unauthenticated visitor hitting
 * /admin/feedback is redirected to Clerk's sign-in flow before the page
 * ever renders. This is the FIRST of two checks - a signed-in Clerk
 * session on its own is not enough to reach real admin data. The
 * second check (is this session's email on ADMIN_ALLOWED_EMAILS) can
 * only happen against the real backend (huntloop.api.admin_auth),
 * which is why /admin/feedback/page.tsx still has to handle a 403 from
 * the API even though this proxy already required a valid session -
 * this layer only proves "someone signed in with Clerk", never "the
 * right person signed in".
 */
import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";

const isAdminRoute = createRouteMatcher(["/admin(.*)"]);

export default clerkMiddleware(async (auth, req) => {
  if (isAdminRoute(req)) {
    await auth.protect();
  }
});

export const config = {
  matcher: ["/((?!_next|.*\\..*).*)", "/(api|trpc)(.*)"],
};
