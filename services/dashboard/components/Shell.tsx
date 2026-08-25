"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { api, signOut } from "@/lib/api";
import { ist } from "@/lib/format";
import type { Operator, PlatformHealth } from "@/lib/types";
import { Spinner } from "./ui";

interface NavItem {
  href: string;
  label: string;
  /** Permission required to see this section, if any. */
  permission?: string;
}

const NAV: { group: string; items: NavItem[] }[] = [
  {
    group: "Operations",
    items: [
      { href: "/", label: "Overview" },
      { href: "/registry", label: "Camera registry", permission: "registry:read" },
      { href: "/events", label: "Federated events", permission: "registry:read" },
      { href: "/detections", label: "Object detections", permission: "detection:read" },
    ],
  },
  {
    group: "Onboarding",
    items: [
      { href: "/installations", label: "Installation requests", permission: "installation:read" },
      { href: "/installations/new", label: "New CCTV installation", permission: "installation:create" },
      { href: "/installations/bulk", label: "Bulk upload (CSV)", permission: "installation:create" },
    ],
  },
  {
    group: "Video access",
    items: [
      {
        href: "/access-requests",
        label: "Access requests",
        permission: "registry:read",
      },
    ],
  },
  {
    group: "Reports",
    items: [{ href: "/reports/gap-analysis", label: "Gap analysis", permission: "registry:read" }],
  },
  {
    group: "Oversight",
    items: [{ href: "/audit", label: "Audit log", permission: "audit:read" }],
  },
];

function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  if (href === "/installations") {
    return pathname === "/installations" || /^\/installations\/(?!new|bulk)/.test(pathname);
  }
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [operator, setOperator] = useState<Operator | null>(null);
  const [health, setHealth] = useState<PlatformHealth | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    api.me().then(setOperator).catch(() => setOperator(null));
    const load = () => api.health().then(setHealth).catch(() => setHealth(null));
    load();
    const timer = setInterval(load, 15_000);
    return () => clearInterval(timer);
  }, []);

  const handleSignOut = useCallback(async () => {
    await signOut();
    router.push("/login");
    router.refresh();
  }, [router]);

  const can = (permission?: string) =>
    !permission || (operator?.permissions ?? []).includes(permission);

  const statusTone =
    health?.status === "ok"
      ? { dot: "bg-ok", label: "All systems operational" }
      : health?.status === "degraded"
        ? { dot: "bg-warn", label: "Degraded — a department system is unreachable" }
        : health?.status === "down"
          ? { dot: "bg-bad", label: "Central registry unavailable" }
          : { dot: "bg-idle", label: "Checking status…" };

  return (
    <div className="flex min-h-screen flex-col">
      {/* ---------------------------------------------------------------- */}
      <header className="sticky top-0 z-30 border-b border-navy-900 bg-navy-800 text-white">
        <div className="flex h-14 items-center gap-4 px-4">
          <button
            className="rounded p-1.5 text-white/70 hover:bg-white/10 lg:hidden"
            onClick={() => setMenuOpen((open) => !open)}
            aria-label="Toggle navigation"
          >
            <svg viewBox="0 0 20 20" className="h-5 w-5" fill="none" aria-hidden>
              <path d="M3 5h14M3 10h14M3 15h14" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
            </svg>
          </button>

          <Link href="/" className="flex items-center gap-2.5">
            <span className="flex h-8 w-8 items-center justify-center rounded border border-white/25 bg-white/10">
              <svg viewBox="0 0 24 24" className="h-4.5 w-4.5" fill="none" aria-hidden>
                <path
                  d="M12 3 4 6.2v5.3c0 4.6 3.2 8.5 8 9.5 4.8-1 8-4.9 8-9.5V6.2L12 3Z"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  strokeLinejoin="round"
                />
                <circle cx="12" cy="11" r="2.4" stroke="currentColor" strokeWidth="1.5" />
              </svg>
            </span>
            <span className="leading-tight">
              <span className="block text-[15px] font-semibold tracking-wide">SENTINEL</span>
              <span className="block text-2xs uppercase tracking-wider text-white/60">
                CCTV Asset Registry
              </span>
            </span>
          </Link>

          <span className="hidden rounded-sm border border-white/25 px-1.5 py-0.5 text-2xs font-semibold uppercase tracking-wider text-white/80 sm:inline">
            {health?.environment ?? "DEMO / MODULE 1"}
          </span>
          <span className="hidden rounded-sm border border-white/25 px-1.5 py-0.5 text-2xs font-semibold uppercase tracking-wider text-white/80 md:inline">
            Metadata only
          </span>

          <div className="ml-auto flex items-center gap-4">
            <div className="hidden items-center gap-2 md:flex" title={
              health?.dependencies.map((d) => `${d.name}: ${d.status}`).join("\n") ?? ""
            }>
              <span className={`h-2 w-2 rounded-full ${statusTone.dot}`} aria-hidden />
              <span className="text-2xs text-white/80">{statusTone.label}</span>
            </div>

            <div className="flex items-center gap-3 border-l border-white/15 pl-4">
              <div className="hidden text-right leading-tight sm:block">
                <div className="text-[13px] font-medium">{operator?.display_name ?? "…"}</div>
                <div className="text-2xs text-white/60">
                  {operator ? `${operator.role.replace(/_/g, " ")} · ${operator.department}` : ""}
                </div>
              </div>
              <button
                className="rounded border border-white/25 px-2 py-1 text-2xs font-medium text-white/85 hover:bg-white/10"
                onClick={handleSignOut}
              >
                Sign out
              </button>
            </div>
          </div>
        </div>
      </header>

      <div className="flex flex-1">
        {/* -------------------------------------------------------------- */}
        <nav
          className={`${
            menuOpen ? "block" : "hidden"
          } w-full shrink-0 border-r border-navy-900 bg-navy-700 lg:block lg:w-56`}
          aria-label="Sections"
        >
          <div className="sticky top-14 py-3">
            {NAV.map((section) => {
              const visible = section.items.filter((item) => can(item.permission));
              if (visible.length === 0) return null;
              return (
                <div key={section.group} className="mb-4">
                  <div className="px-4 pb-1 text-2xs font-semibold uppercase tracking-wider text-white/40">
                    {section.group}
                  </div>
                  {visible.map((item) => {
                    const active = isActive(pathname, item.href);
                    return (
                      <Link
                        key={item.href}
                        href={item.href}
                        onClick={() => setMenuOpen(false)}
                        aria-current={active ? "page" : undefined}
                        className={`block border-l-[3px] px-4 py-1.5 text-[13px] transition ${
                          active
                            ? "border-l-white bg-navy-600 font-medium text-white"
                            : "border-l-transparent text-white/70 hover:bg-navy-600/60 hover:text-white"
                        }`}
                      >
                        {item.label}
                      </Link>
                    );
                  })}
                </div>
              );
            })}

            <div className="mx-4 mt-6 rounded border border-white/12 bg-navy-800/70 p-2.5">
              <div className="flex items-center gap-1.5 text-2xs font-semibold uppercase tracking-wider text-white/70">
                <svg viewBox="0 0 16 16" className="h-3 w-3" fill="none" aria-hidden>
                  <rect x="3" y="7" width="10" height="7" rx="1.5" stroke="currentColor" strokeWidth="1.3" />
                  <path d="M5.5 7V5a2.5 2.5 0 0 1 5 0v2" stroke="currentColor" strokeWidth="1.3" />
                </svg>
                No video access
              </div>
              <p className="mt-1 text-2xs leading-relaxed text-white/55">
                Footage stays with the owning department. Sentinel holds metadata, health and policy
                records only.
              </p>
            </div>
          </div>
        </nav>

        {/* -------------------------------------------------------------- */}
        <main className="min-w-0 flex-1">
          <div className="mx-auto max-w-[1500px] px-4 py-5">{children}</div>
          <footer className="border-t border-line px-4 py-3 text-2xs text-ink-400">
            Sentinel Module 1 · metadata-only federated CCTV registry · synthetic demonstration data
            · timestamps in Asia/Kolkata
            {health?.time_utc && <> · registry time {ist(health.time_utc)}</>}
          </footer>
        </main>
      </div>
    </div>
  );
}

export function LoadingPanel({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 px-4 py-10 text-[13px] text-ink-500">
      <Spinner /> {label}…
    </div>
  );
}
