"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV_ITEMS = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/jobs", label: "Jobs" },
  { href: "/applications", label: "Applications" },
  { href: "/resumes", label: "Resume" },
];

export function NavBar() {
  const pathname = usePathname();

  return (
    <div
      className="sticky top-0 z-30 border-b border-border backdrop-blur-md"
      style={{ backgroundColor: "rgba(249,248,246,.88)" }}
    >
      <div className="mx-auto flex h-[58px] max-w-6xl items-center gap-3 px-6">
        <Link href="/dashboard" className="mr-2 flex items-center gap-2">
          <span className="grid h-[26px] w-[26px] place-items-center rounded-[7px] bg-accent shadow-[0_2px_6px_rgba(224,83,61,.35)]">
            <span className="font-mono text-sm font-bold text-white">H</span>
          </span>
          <span className="font-mono text-base font-semibold tracking-tight">HuntLoop</span>
        </Link>
        <nav className="flex items-center gap-1">
          {NAV_ITEMS.map((item) => {
            // /jobs/[id] should still highlight the Jobs tab.
            const isActive = item.href === "/jobs" ? pathname.startsWith("/jobs") : pathname === item.href;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`rounded-lg border px-3.5 py-1.5 font-mono text-xs font-medium ${
                  isActive
                    ? "border-border bg-surface text-text"
                    : "border-transparent text-text-muted hover:text-text"
                }`}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>
      </div>
    </div>
  );
}
