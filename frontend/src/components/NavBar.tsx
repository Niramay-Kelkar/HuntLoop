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
    <div className="sticky top-0 z-30 border-b border-border bg-surface">
      <div className="mx-auto flex min-h-[54px] max-w-6xl flex-wrap items-center gap-x-4 gap-y-1 px-6 py-2">
        <Link href="/dashboard" className="mr-3 flex items-center gap-2">
          <span className="grid h-[24px] w-[24px] place-items-center rounded-sm bg-accent">
            <span className="font-mono text-sm font-semibold text-white">H</span>
          </span>
          <span className="font-mono text-base font-semibold tracking-tight">HuntLoop</span>
        </Link>
        <nav className="flex flex-wrap items-center gap-0.5">
          {NAV_ITEMS.map((item) => {
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
      </div>
    </div>
  );
}
