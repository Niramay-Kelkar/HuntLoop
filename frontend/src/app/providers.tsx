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
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            // With the default retry (3 attempts w/ back-off) a failed
            // query never surfaces its error under React 19 + Query v5
            // here - the retryer stalls and the view is stuck on its
            // loading skeleton forever (verified against a 404 job id
            // and an unreachable API). Retrying is left to the explicit
            // "Try again" buttons in ErrorState, which call refetch().
            retry: 0,
            staleTime: 15_000,
          },
        },
      }),
  );

  return (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>{children}</ToastProvider>
    </QueryClientProvider>
  );
}
