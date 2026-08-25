"use client";

import { usePathname } from "next/navigation";

import { Shell } from "./Shell";

/**
 * The sign-in page is the one route without the console chrome - showing a
 * navigation rail to someone who is not signed in would be misleading.
 */
export function AppFrame({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  if (pathname === "/login") return <>{children}</>;
  return <Shell>{children}</Shell>;
}
