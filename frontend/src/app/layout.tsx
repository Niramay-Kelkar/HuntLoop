import type { Metadata } from "next";
import { ClerkProvider } from "@clerk/nextjs";
import { Public_Sans, IBM_Plex_Mono } from "next/font/google";
import "./globals.css";
import { DemoBanner } from "@/components/DemoBanner";
import { DemoWakeUpGate } from "@/components/DemoWakeUpGate";
import { FeedbackTrigger } from "@/components/FeedbackTrigger";
import { NavBar } from "@/components/NavBar";
import { Providers } from "./providers";

// Visual identity: Direction A, "Register". Public Sans carries all
// structural type - it is the typeface of US federal websites, and this
// app runs on DOL wage-filing data. The font variable is put FIRST in
// the CSS stack (see globals.css) so it actually renders; the previous
// setup led its stack with "Helvetica Neue" and the declared web font
// never reached the page on macOS.
const publicSans = Public_Sans({
  subsets: ["latin"],
  variable: "--font-public-sans",
  display: "swap",
});

// IBM Plex Mono for every figure: match scores, filing counts, dates,
// all tabular. Not a variable font on Google Fonts, so weights are
// explicit.
const plexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-plex-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "HuntLoop",
  description: "HuntLoop job-search dashboard",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <ClerkProvider>
      <html
        lang="en"
        className={`${publicSans.variable} ${plexMono.variable} h-full antialiased`}
      >
        <body className="min-h-full flex flex-col bg-bg text-text font-sans">
          <Providers>
            <DemoBanner />
            <NavBar />
            <div className="mx-auto w-full max-w-6xl flex-1 px-6 py-7">
              <DemoWakeUpGate>{children}</DemoWakeUpGate>
            </div>
            <FeedbackTrigger />
          </Providers>
        </body>
      </html>
    </ClerkProvider>
  );
}
