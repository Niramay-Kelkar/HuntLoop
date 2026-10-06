import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Hide the Next.js dev-mode indicator (the "N" button that renders in
  // the bottom-left corner during `next dev`). It never appears in a
  // production build, but there's no reason to show the framework's
  // badge over our own UI while developing either. Compile and runtime
  // errors are still surfaced on screen.
  devIndicators: false,
  // Nothing in this app imports next/image or otherwise uses Next's
  // built-in Image Optimization API (confirmed - no match anywhere
  // under src/ for "image", case-insensitive), but that /_next/image
  // route is baked into every Next.js app regardless and stays
  // reachable on the deployed container unless explicitly turned off -
  // it's backed by `sharp`, which has carried real high-severity CVEs
  // (see SESSIONS.md). `unoptimized: true` is Next's own documented way
  // to disable it; verified directly against this installed Next
  // version's own server source
  // (node_modules/next/dist/server/next-server.js) that it makes the
  // route return a real 404 before the optimizer/sharp is ever reached,
  // not a silent no-op.
  images: {
    unoptimized: true,
  },
};

export default nextConfig;
