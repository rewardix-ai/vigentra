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
  type LucideIcon,
} from "lucide-react";

import { api, signOut } from "@/lib/api";
import { BrandLockup, VigentraMark } from "./Brand";
import type { Operator, PlatformHealth } from "@/lib/types";
import { Spinner } from "./ui";

/** Queues whose outstanding item count is worth surfacing in the rail. */
type BadgeKey = "installations" | "alerts";

interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  /** Permission required to see this section, if any. */
  /** Shown when the operator holds this permission, or any one of a list. */
  permission?: string | string[];
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
      { href: "/alerts", label: "Alerts", icon: BellRing, permission: ["alert:read", "health:read"], badge: "alerts" },
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
      // Watchlist hits and cameras down, in one count: both are things someone should look at now.
      if (permissions.includes("alert:read") || permissions.includes("health:read")) {
        void Promise.all([
          permissions.includes("alert:read") ? api.openAlertCount().then((c) => c.open).catch(() => 0) : 0,
          permissions.includes("health:read") ? api.openHealthAlertCount().then((c) => c.open).catch(() => 0) : 0,
        ]).then(([hits, down]) => set("alerts", hits + down));
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

  const can = (permission?: string | string[]) =>
    !permission || [permission].flat().some((p) => (operator?.permissions ?? []).includes(p));

  const statusTone =
    health?.status === "ok"
      ? { dot: "bg-ok", label: "All systems operational" }
      : health?.status === "degraded"
        ? { dot: "bg-warn", label: "Degraded — a department system is unreachable" }
        : health?.status === "down"
          ? { dot: "bg-bad", label: "Central registry unavailable" }
          : { dot: "bg-faint", label: "Checking status…" };

  return (
    <div className="flex min-h-screen flex-col">
      {/* ---------------------------------------------------------------- */}
      {/* The navigation is a stadium that floats clear of the viewport edge,
          with the page canvas visibly wrapping it. The strip it sits in is
          white and sticky, so a scrolling table disappears behind the canvas
          rather than showing through the pill's rounded ends. */}
      <header className="sticky top-0 z-30 bg-canvas px-4 pb-2 pt-3">
        <div className="mx-auto flex h-14 w-full max-w-[1760px] items-center gap-2 rounded-full bg-canvas-soft pl-2 pr-2 lg:pl-5">
          <button
            className="btn btn-icon btn-soft lg:hidden"
            onClick={() => setMenuOpen((open) => !open)}
            aria-label="Toggle navigation"
            aria-expanded={menuOpen}
          >
            <Menu className="h-5 w-5" strokeWidth={1.8} aria-hidden />
          </button>

          <Link href="/" className="flex shrink-0 items-center rounded-full">
            {/* The pill tightens to the mark alone where the lockup will not fit. */}
            <span className="sm:hidden">
              <VigentraMark labelled className="h-8 w-auto" />
            </span>
            <span className="hidden sm:block">
              <BrandLockup />
            </span>
          </Link>

          {/* What the pill carries is decided by what fits: the status line
              joins once the rail is beside the page rather than behind the menu
              button, the operator's name a step before that. */}
          <div className="ml-auto flex min-w-0 items-center gap-4">
            <div className="hidden shrink-0 items-center gap-2 lg:flex" title={
              health?.dependencies.map((d) => `${d.name}: ${d.status}`).join("\n") ?? ""
            }>
              <span className={`h-2 w-2 rounded-full ${statusTone.dot}`} aria-hidden />
              <span className="text-caption text-ink/65">{statusTone.label}</span>
            </div>

            <div className="flex min-w-0 items-center gap-3">
              <div className="hidden min-w-0 text-right md:block">
                <div className="truncate text-body-sm font-semibold leading-tight text-ink">
                  {operator?.display_name ?? "…"}
                </div>
                <div className="truncate text-caption text-ink/65">
                  {operator
                    ? `${operator.role.replace(/_/g, " ")} · ${
                        operator.department === "*" ? "all departments" : operator.department
                      }`
                    : ""}
                </div>
              </div>
              <button className="btn shrink-0" onClick={handleSignOut}>
                Sign out
              </button>
            </div>
          </div>
        </div>
      </header>

      <div className="mx-auto flex w-full max-w-[1792px] flex-1 flex-col px-4 lg:flex-row">
        {/* -------------------------------------------------------------- */}
        <nav
          className={`${menuOpen ? "block" : "hidden"} w-full shrink-0 lg:block lg:w-64`}
          aria-label="Sections"
        >
          {/* Scrolls on its own: the rail is taller than a laptop screen once an
              account can see every section. 4.75rem is the header above it
              (0.75 + 3.5 + 0.5), in rem so it follows the console's size. */}
          <div className="sticky top-[4.75rem] max-h-[calc(100vh-4.75rem)] overflow-y-auto pb-8 pt-2 lg:pr-4">
            {NAV.map((section) => ({
              ...section,
              items: section.items.filter((item) => can(item.permission)),
            }))
              // Resolve the visible sections before rendering, so the gap sits
              // between them rather than above whichever section happens to be
              // first once the operator's permissions have filtered the rail.
              .filter((section) => section.items.length > 0)
              .map((section, index) => (
                <div key={section.group} className={index === 0 ? "" : "mt-6"}>
                  <div className="px-4 pb-2 text-caption text-muted">{section.group}</div>
                  <div className="space-y-0.5">
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
                          // The current section is ink on the tint; everything
                          // else is muted on white. The icon takes the label's
                          // colour, so the two always change together.
                          className={`flex items-center gap-3 rounded-sm px-4 py-2 text-body-sm transition-colors duration-150 ${
                            active
                              ? "bg-canvas-soft font-semibold text-ink"
                              : "text-muted hover:bg-canvas-soft hover:text-ink"
                          }`}
                        >
                          <Icon className="h-4 w-4 shrink-0" strokeWidth={1.8} aria-hidden />
                          <span className="min-w-0 flex-1 truncate">{item.label}</span>
                          {/* The one place the accent is spent: a queue with
                              something in it is asking for a decision. */}
                          {count > 0 && (
                            <span
                              className="shrink-0 rounded-full bg-accent px-2 py-0.5 text-label tabular-nums text-on-primary"
                              aria-label={`${count} outstanding`}
                            >
                              {count > 99 ? "99+" : count}
                            </span>
                          )}
                        </Link>
                      );
                    })}
                  </div>
                </div>
              ))}
          </div>
        </nav>

        {/* -------------------------------------------------------------- */}
        <main className="min-w-0 flex-1 pb-section pt-2 lg:pl-4">{children}</main>
      </div>

      <PageFooter />
    </div>
  );
}

/**
 * Every page ends on the same inverted band - ink where the rest is white,
 * rounded where it meets the canvas. Shared with the sign-in page, which has
 * no shell around it but should still end the way the console does.
 */
export function PageFooter() {
  return (
    <footer className="mx-auto w-full max-w-[1792px] px-4">
      <div className="flex flex-wrap items-center justify-between gap-x-8 gap-y-3 rounded-t-md bg-ink px-6 py-6 sm:px-8">
        <BrandLockup tone="onDark" />
        <p className="text-caption text-faint">All times are shown in IST</p>
      </div>
    </footer>
  );
}

export function LoadingPanel({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 py-12 text-body-sm text-muted">
      <Spinner /> {label}…
    </div>
  );
}
