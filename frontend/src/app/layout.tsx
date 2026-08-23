import type { Metadata } from "next";
import { Geist, JetBrains_Mono } from "next/font/google";
import Link from "next/link";
import "./globals.css";
import { Providers } from "./providers";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

// The mockup (design/HuntLoop.dc.html) uses JetBrains Mono throughout for
// nav items, numerics, labels and badges, alongside a plain sans body font -
// replaces Geist Mono, which the mockup never uses.
const jetbrainsMono = JetBrains_Mono({
  variable: "--font-jetbrains-mono",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
});

export const metadata: Metadata = {
  title: "HuntLoop",
  description: "HuntLoop job-search dashboard",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${jetbrainsMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col bg-bg text-text">
        <Providers>
          <div
            className="sticky top-0 z-30 border-b border-border backdrop-blur-md"
            style={{ backgroundColor: "rgba(249,248,246,.88)" }}
          >
            <div className="mx-auto flex h-[58px] max-w-6xl items-center gap-3 px-6">
              <Link href="/jobs" className="mr-2 flex items-center gap-2">
                <span className="grid h-[26px] w-[26px] place-items-center rounded-[7px] bg-accent shadow-[0_2px_6px_rgba(224,83,61,.35)]">
                  <span className="font-mono text-sm font-bold text-white">H</span>
                </span>
                <span className="font-mono text-base font-semibold tracking-tight">HuntLoop</span>
              </Link>
              <nav className="flex items-center gap-1">
                <Link
                  href="/jobs"
                  className="rounded-lg border border-transparent px-3.5 py-1.5 font-mono text-xs font-medium text-text-muted hover:text-text"
                >
                  Jobs
                </Link>
                <Link
                  href="/applications"
                  className="rounded-lg border border-transparent px-3.5 py-1.5 font-mono text-xs font-medium text-text-muted hover:text-text"
                >
                  Applications
                </Link>
              </nav>
            </div>
          </div>
          <div className="mx-auto w-full max-w-6xl flex-1 px-6 py-7">{children}</div>
        </Providers>
      </body>
    </html>
  );
}
