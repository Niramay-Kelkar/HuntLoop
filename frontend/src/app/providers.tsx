"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";

import { ToastProvider } from "@/components/Toast";

/**
 * TanStack Query + toast providers. A client component (App Router
 * server components can't hold React context/hooks directly) - the
 * QueryClient is created once per browser session via useState, not
 * module scope, so it isn't accidentally shared across requests during
 * SSR.
 */
export function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(() => new QueryClient());

  return (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>{children}</ToastProvider>
    </QueryClientProvider>
  );
}
