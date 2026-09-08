import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Hide the Next.js dev-mode indicator (the "N" button that renders in
  // the bottom-left corner during `next dev`). It never appears in a
  // production build, but there's no reason to show the framework's
  // badge over our own UI while developing either. Compile and runtime
  // errors are still surfaced on screen.
  devIndicators: false,
};

export default nextConfig;
