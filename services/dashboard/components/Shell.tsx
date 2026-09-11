"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import {
  BellRing,
  Cctv,
  ClipboardList,
  FileBarChart2,
  FilePlus2,
  KeyRound,
  LayoutDashboard,
  ListChecks,
  MapPinned,
  Menu,
  MonitorPlay,
  Radio,
  Route,
  TriangleAlert,
  ScanEye,
  ScrollText,
  Upload,
  Video,
  type LucideIcon,
} from "lucide-react";

import { api, signOut } from "@/lib/api";
import { BrandLockup } from "./Brand";
import type { Operator, PlatformHealth } from "@/lib/types";
import { Spinner } from "./ui";

/** Queues whose outstanding item count is worth surfacing in the rail. */
type BadgeKey = "installations" | "alerts";

interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  /** Permission required to see this section, if any. */
  permission?: string;
  /** Shows a count pill when that queue is non-empty. */
  badge?: BadgeKey;
}

const NAV: { group: string; items: NavItem[] }[] = [
  {
    group: "Operations",
    items: [
      { href: "/", label: "Overview", icon: LayoutDashboard },
      { href: "/registry", label: "Camera registry", icon: Cctv, permission: "registry:read" },
      { href: "/live", label: "Live wall", icon: MonitorPlay, permission: "video:live" },
      { href: "/events", label: "Federated events", icon: Radio, permission: "registry:read" },
      { href: "/detections", label: "Object detections", icon: ScanEye, permission: "detection:read" },
      { href: "/incidents", label: "Incident review", icon: TriangleAlert, permission: "detection:read" },
    ],
  },
  {
    group: "Onboarding",
    items: [
      // First, and the only way in: raising a camera is an installer's main
      // job, so it leads the section rather than hiding on the list page.
      { href: "/installations/new", label: "New CCTV installation", icon: FilePlus2, permission: "installation:create" },
      {
        href: "/installations",
        label: "Installation requests",
        icon: ClipboardList,
        permission: "installation:read",
        badge: "installations",
      },
      { href: "/installations/bulk", label: "Bulk upload (CSV)", icon: Upload, permission: "installation:create" },
    ],
  },
  {
    group: "Video access",
    items: [
      {
        href: "/access-requests",
        label: "Access requests",
        icon: KeyRound,
        permission: "registry:read",
      },
    ],
  },
  {
    // Four separate permissions, so a role can hold any of these without the
    // others - an auditor reads alerts and traces but never edits the list,
    // and the municipal roles hold none of them at all.
    group: "Vehicles of interest",
    items: [
      { href: "/alerts", label: "Alerts", icon: BellRing, permission: "alert:read", badge: "alerts" },
      { href: "/plates", label: "Trace a vehicle", icon: Route, permission: "track:read" },
      { href: "/watchlist", label: "Watchlist", icon: ListChecks, permission: "watchlist:read" },
    ],
  },
  {
    group: "Reports",
    items: [
      { href: "/reports/gap-analysis", label: "Gap analysis", icon: MapPinned, permission: "registry:read" },
      { href: "/reports/anpr", label: "ANPR output report", icon: FileBarChart2, permission: "plate:read" },
    ],
  },
  {
    group: "Oversight",
    items: [{ href: "/audit", label: "Audit log", icon: ScrollText, permission: "audit:read" }],
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
  const [badges, setBadges] = useState<Partial<Record<BadgeKey, number>>>({});
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    api.me().then(setOperator).catch(() => setOperator(null));
    const load = () => api.health().then(setHealth).catch(() => setHealth(null));
    load();
    const timer = setInterval(load, 15_000);
    return () => clearInterval(timer);
  }, []);

  // Queue counts, refreshed on the same cadence as health. Each is fetched only
  // when the operator can actually open that queue, so a municipal role never
  // triggers a 403 for a section it cannot see.
  const permissions = operator?.permissions;
  useEffect(() => {
    if (!permissions) return;
    let cancelled = false;
    const set = (key: BadgeKey, value: number) =>
      !cancelled && setBadges((prev) => ({ ...prev, [key]: value }));

    const load = () => {
      if (permissions.includes("installation:read")) {
        api
          .overview()
          .then((o) => set("installations", o.pending_installation_requests))
          .catch(() => undefined);
      }
      if (permissions.includes("alert:read")) {
        api
          .alerts({ unacknowledged_only: "true", limit: "100" })
          .then((rows) => set("alerts", rows.length))
          .catch(() => undefined);
      }
    };

    load();
    const timer = setInterval(load, 15_000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [permissions]);

  const handleSignOut = useCallback(async () => {
    await signOut();
    router.push("/login");
    router.refresh();
  }, [router]);

  const can = (permission?: string) =>
    !permission || (operator?.permissions ?? []).includes(permission);

  /** Whether this account may be handed footage at all, live or recorded. */

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
            <Menu className="h-5 w-5" strokeWidth={1.6} aria-hidden />
          </button>

          <Link href="/" className="flex items-center gap-2.5">
            <BrandLockup tone="onDark" />
          </Link>

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
            {NAV.map((section) => ({
              ...section,
              items: section.items.filter((item) => can(item.permission)),
            }))
              // Resolve the visible sections before rendering, so the rule sits
              // between them rather than above whichever section happens to be
              // first once the operator's permissions have filtered the rail.
              .filter((section) => section.items.length > 0)
              .map((section, index) => (
                <div
                  key={section.group}
                  className={
                    index === 0
                      ? "mb-4"
                      : "mb-4 border-t border-white/10 pt-4"
                  }
                >
                  <div className="px-4 pb-1 text-2xs font-semibold uppercase tracking-wider text-white/40">
                    {section.group}
                  </div>
                  {section.items.map((item) => {
                    const active = isActive(pathname, item.href);
                    const Icon = item.icon;
                    const count = item.badge ? badges[item.badge] ?? 0 : 0;
                    return (
                      <Link
                        key={item.href}
                        href={item.href}
                        onClick={() => setMenuOpen(false)}
                        aria-current={active ? "page" : undefined}
                        className={`flex items-center gap-2.5 border-l-[3px] px-4 py-1.5 text-[13px] transition ${
                          active
                            ? "border-l-white bg-navy-600 font-medium text-white"
                            : "border-l-transparent text-white/70 hover:bg-navy-600/60 hover:text-white"
                        }`}
                      >
                        <Icon
                          className={`h-4 w-4 shrink-0 ${active ? "text-white" : "text-white/55"}`}
                          strokeWidth={1.6}
                          aria-hidden
                        />
                        <span className="min-w-0 flex-1 truncate">{item.label}</span>
                        {count > 0 && (
                          <span
                            className="shrink-0 rounded-full bg-white/15 px-1.5 py-0.5 text-2xs font-semibold tabular-nums text-white"
                            aria-label={`${count} outstanding`}
                          >
                            {count > 99 ? "99+" : count}
                          </span>
                        )}
                      </Link>
                    );
                  })}
                </div>
              ))}

          </div>
        </nav>

        {/* -------------------------------------------------------------- */}
        <main className="min-w-0 flex-1">
          <div className="mx-auto max-w-[1500px] px-4 py-5">{children}</div>
          <footer className="border-t border-line px-4 py-3 text-2xs text-ink-400">
            Vigentra · All times are shown in IST
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
