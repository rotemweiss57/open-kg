"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Tooltip } from "radix-ui";
import { useState } from "react";
import { Toaster } from "@/components/toast";

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(() => new QueryClient({
    defaultOptions: { queries: { staleTime: 5_000, retry: 1, refetchOnWindowFocus: true } },
  }));
  return (
    <QueryClientProvider client={client}>
      <Tooltip.Provider delayDuration={250}>
        {children}
        <Toaster />
      </Tooltip.Provider>
    </QueryClientProvider>
  );
}
