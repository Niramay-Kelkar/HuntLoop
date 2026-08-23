import type { Metadata } from "next";
import { Geist, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { NavBar } from "@/components/NavBar";
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
          <NavBar />
          <div className="mx-auto w-full max-w-6xl flex-1 px-6 py-7">{children}</div>
        </Providers>
      </body>
    </html>
  );
}
