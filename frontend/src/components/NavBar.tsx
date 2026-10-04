"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuth } from "@clerk/nextjs";
import { useQuery } from "@tanstack/react-query";

import { DraftSettingsModal } from "@/components/DraftSettingsModal";
import { getAdminWhoami } from "@/lib/adminApi";
import { isDemoMode } from "@/lib/demoMode";

const NAV_ITEMS = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/jobs", label: "Jobs" },
  { href: "/applications", label: "Applications" },
  { href: "/resumes", label: "Resume" },
];

/**
 * Whether to show the Admin nav link - true only once GET /admin/whoami
 * (huntloop.api.routers.admin) actually succeeds for this signed-in
 * Clerk session, never from a frontend-side copy of ADMIN_ALLOWED_EMAILS
 * (see lib/adminApi.ts's getAdminWhoami docstring for why). The query is
 * disabled entirely for a signed-out visitor - the overwhelming majority
 * of public-demo traffic - so this costs nothing for them.
 *
 * The query key includes userId (not just a fixed "admin-whoami"
 * string) specifically so a client-side sign-out, or switching to a
 * different Clerk account, doesn't leave a stale successful result
 * around - `enabled: false` alone does NOT clear a query's previous
 * data/isSuccess in TanStack Query, so without this the Admin link
 * would keep showing after sign-out until a full page reload (caught
 * live while verifying this: sign out via the SPA, client-navigate
 * elsewhere, and the link was still there). A signed-out userId is
 * `null`/`undefined`, giving a key this hook never actually fetches for
 * (enabled stays false), so it carries no stale data of its own either.
 */
function useIsAdmin(): boolean {
  const { isSignedIn, userId, getToken } = useAuth();

  const whoami = useQuery({
    queryKey: ["admin-whoami", userId],
    queryFn: async () => {
      const token = await getToken();
      if (!token) throw new Error("Not signed in.");
      return getAdminWhoami(token);
    },
    enabled: isSignedIn === true,
    staleTime: Infinity,
    retry: false,
  });

  return whoami.isSuccess;
}

export function NavBar() {
  const pathname = usePathname();
  const isAdmin = useIsAdmin();
  const navItems = isAdmin ? [...NAV_ITEMS, { href: "/admin/feedback", label: "Admin" }] : NAV_ITEMS;

  return (
    <div className="sticky top-0 z-30 border-b border-border bg-surface">
      <div className="mx-auto flex min-h-[54px] max-w-6xl flex-wrap items-center gap-x-4 gap-y-1 px-6 py-2">
        <Link href="/dashboard" className="mr-3 flex items-center gap-2">
          <span className="grid h-[24px] w-[24px] place-items-center rounded-sm bg-accent">
            <span className="font-mono text-sm font-semibold text-white">H</span>
          </span>
          <span className="font-mono text-base font-semibold tracking-tight">HuntLoop</span>
        </Link>
        <nav className="flex flex-wrap items-center gap-0.5">
          {navItems.map((item) => {
            // /jobs/[id] should still highlight the Jobs tab.
            const isActive = item.href === "/jobs" ? pathname.startsWith("/jobs") : pathname === item.href;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`border-b-2 px-2.5 py-1.5 font-mono text-xs font-medium ${
                  isActive
                    ? "border-accent text-text"
                    : "border-transparent text-text-subtle hover:text-text"
                }`}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>
        {!isDemoMode() && (
          <div className="ml-auto">
            <DraftSettingsModal />
          </div>
        )}
      </div>
    </div>
  );
}
