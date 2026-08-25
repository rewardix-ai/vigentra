"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { LoadingPanel } from "@/components/Shell";
import {
  Card,
  DepartmentTag,
  FootageNotice,
  Notice,
  PageHeader,
  Pill,
  Spinner,
} from "@/components/ui";
import { api } from "@/lib/api";
import { ist, latency, relative } from "@/lib/format";
import type { Operator, Overview, SourceSystem, SyncResponse } from "@/lib/types";

function Stat({
  label,
  value,
  hint,
  tone = "plain",
  href,
}: {
  label: string;
  value: number | string;
  hint?: string;
  tone?: "plain" | "ok" | "warn" | "bad";
  href?: string;
}) {
  const colour = {
    plain: "text-ink-900",
    ok: "text-ok",
    warn: "text-warn",
    bad: "text-bad",
  }[tone];

  const body = (
    <>
      <div className="field-label">{label}</div>
      <div className={`tabular mt-1 text-[26px] font-semibold leading-none ${colour}`}>{value}</div>
      {hint && <div className="mt-1.5 text-2xs text-ink-500">{hint}</div>}
    </>
  );

  if (href) {
    return (
      <Link href={href} className="card block px-4 py-3 transition hover:border-brand-500 hover:shadow-raised">
        {body}
      </Link>
    );
  }
  return <div className="card px-4 py-3">{body}</div>;
}

export default function OverviewPage() {
  const [operator, setOperator] = useState<Operator | null>(null);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [sources, setSources] = useState<SourceSystem[]>([]);
  const [syncing, setSyncing] = useState(false);
  const [result, setResult] = useState<SyncResponse | null>(null);
  const [message, setMessage] = useState<{ tone: "ok" | "warn" | "bad"; text: string } | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    const [me, counts, systems] = await Promise.allSettled([
      api.me(),
      api.overview(),
      api.sources(),
    ]);
    if (me.status === "fulfilled") setOperator(me.value);
    if (counts.status === "fulfilled") setOverview(counts.value);
    if (systems.status === "fulfilled") setSources(systems.value);
    setLoading(false);
  }, []);

  useEffect(() => {
    void load();
    const timer = setInterval(load, 15_000);
    return () => clearInterval(timer);
  }, [load]);

  const canSync = (operator?.permissions ?? []).includes("installation:sync");

  const runSync = useCallback(async () => {
    setSyncing(true);
    setMessage(null);
    try {
      const response = await api.syncAll();
      setResult(response);
      const failed = Object.entries(response.sources).filter(([, item]) => item.errors.length > 0);
      const skipped = Object.values(response.sources).reduce(
        (total, item) => total + item.skipped_unregistered,
        0,
      );
      if (failed.length === 0) {
        setMessage({
          tone: "ok",
          text:
            `Metadata synchronised from ${Object.keys(response.sources).length} department ` +
            `system(s). ${response.total_cameras} cameras in the registry` +
            (skipped ? `; ${skipped} record(s) still awaiting departmental approval were skipped.` : "."),
        });
      } else {
        setMessage({
          tone: "warn",
          text:
            `${failed.map(([name]) => name).join(", ")} could not be reached. Other departments ` +
            "synchronised normally and their records are unaffected.",
        });
      }
      await load();
    } catch (error) {
      setMessage({ tone: "bad", text: error instanceof Error ? error.message : String(error) });
    } finally {
      setSyncing(false);
    }
  }, [load]);

  if (loading) return <LoadingPanel label="Loading operations overview" />;

  return (
    <>
      <PageHeader
        title="Operations overview"
        subtitle={
          operator
            ? `Signed in as ${operator.display_name} · ${operator.role.replace(/_/g, " ")} · ${operator.department}`
            : undefined
        }
        actions={
          canSync && (
            <button className="btn btn-primary" onClick={runSync} disabled={syncing}>
              {syncing ? <Spinner /> : null}
              {syncing ? "Synchronising…" : "Synchronise metadata"}
            </button>
          )
        }
      />

      <div className="space-y-4">
        {/* Only for accounts that hold no footage rights at all. Telling a
            viewing account that video lives elsewhere is just wrong. */}
        {operator && !operator.sentinel_video_access && <FootageNotice />}

        {message && <Notice tone={message.tone}>{message.text}</Notice>}

        {/* Registry counts */}
        <div>
          <h2 className="section-label mb-2">Camera register</h2>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
            <Stat
              label="Registered cameras"
              value={overview?.total_cameras ?? "—"}
              hint="every unit's cameras, in one registry"
              href="/registry"
            />
            <Stat
              label="Commissioned"
              value={overview?.approved_cameras ?? "—"}
              hint="in service"
              tone="ok"
              href="/registry?installation_status=COMMISSIONED"
            />
            <Stat
              label="Suspended"
              value={overview?.suspended_cameras ?? "—"}
              hint="withdrawn by the owning department"
              tone={overview && overview.suspended_cameras > 0 ? "warn" : "plain"}
              href="/registry?installation_status=SUSPENDED"
            />
            <Stat
              label="Decommissioned"
              value={overview?.decommissioned_cameras ?? "—"}
              hint="retired assets, retained for record"
              href="/registry?installation_status=DECOMMISSIONED"
            />
            <Stat
              label="Departments connected"
              value={overview ? `${overview.departments_connected}/${overview.departments_total}` : "—"}
              hint="federated CCTV/VMS systems"
            />
          </div>
        </div>

        {/* Pipeline + health */}
        <div className="grid gap-3 lg:grid-cols-2">
          <div>
            <h2 className="section-label mb-2">Onboarding pipeline</h2>
            <div className="grid grid-cols-3 gap-3">
              <Stat
                label="Not yet in registry"
                value={overview?.pending_installation_requests ?? "—"}
                hint="still with the unit that raised it"
                href="/installations"
              />
              <Stat
                label="Drafts"
                value={overview?.draft_installation_requests ?? "—"}
                hint="not yet submitted"
                href="/installations?status=DRAFT"
              />
              <Stat
                label="Awaiting sync"
                value={overview?.requiring_metadata_review ?? "—"}
                hint="registered, not yet pulled across"
                tone={overview && overview.requiring_metadata_review > 0 ? "warn" : "plain"}
                href="/installations?status=REGISTERED"
              />
            </div>
          </div>

          <div>
            <h2 className="section-label mb-2">Camera health</h2>
            <div className="grid grid-cols-4 gap-3">
              <Stat label="Online" value={overview?.online ?? "—"} tone="ok" />
              <Stat
                label="Degraded"
                value={overview?.degraded ?? "—"}
                tone={overview && overview.degraded > 0 ? "warn" : "plain"}
              />
              <Stat
                label="Offline"
                value={overview?.offline ?? "—"}
                tone={overview && overview.offline > 0 ? "bad" : "plain"}
              />
              <Stat label="Unavailable" value={overview?.unavailable ?? "—"} hint="withdrawn" />
            </div>
          </div>
        </div>

        {/* Department systems */}
        <Card
          title="Federated department systems"
          action={
            <span className="text-2xs text-ink-500">
              Last metadata synchronisation: {ist(overview?.last_metadata_sync_at)}
            </span>
          }
        >
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Department</th>
                  <th>System</th>
                  <th>Adapter</th>
                  <th>Endpoint</th>
                  <th className="text-right">Cameras</th>
                  <th className="text-right">Pending</th>
                  <th>Status</th>
                  <th>Round trip</th>
                  <th>Last sync</th>
                </tr>
              </thead>
              <tbody>
                {sources.map((source) => {
                  const result_ = result?.sources[source.source_system];
                  return (
                    <tr key={source.source_system}>
                      <td>
                        <DepartmentTag department={source.department} />
                      </td>
                      <td className="font-medium">{source.display_name}</td>
                      <td className="text-ink-500">
                        {source.adapter} v{source.adapter_version}
                      </td>
                      <td className="mono text-ink-500">{source.endpoint}</td>
                      <td className="tabular text-right">{source.camera_count}</td>
                      <td className="tabular text-right">{source.pending_requests}</td>
                      <td>
                        <Pill tone={source.status === "online" ? "ok" : source.status === "degraded" ? "warn" : "bad"}>
                          {source.status}
                        </Pill>
                        {result_ && result_.skipped_unregistered > 0 && (
                          <div className="mt-1 text-2xs text-ink-500">
                            {result_.skipped_unregistered} not yet registered
                          </div>
                        )}
                      </td>
                      <td className="tabular text-ink-500">{latency(source.latency_ms)}</td>
                      <td className="text-ink-500" title={ist(source.last_sync_at)}>
                        {relative(source.last_sync_at)}
                      </td>
                    </tr>
                  );
                })}
                {sources.length === 0 && (
                  <tr>
                    <td colSpan={9} className="py-8 text-center text-ink-500">
                      No department systems registered.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          {sources.some((source) => source.last_error) && (
            <div className="border-t border-line p-3">
              {sources
                .filter((source) => source.last_error)
                .map((source) => (
                  <Notice key={source.source_system} tone="bad">
                    <strong>{source.display_name}:</strong> {source.last_error}
                  </Notice>
                ))}
            </div>
          )}
          <p className="border-t border-line px-4 py-2 text-2xs leading-relaxed text-ink-500">
            Each department runs its own CCTV/VMS system and its own installation register.
            Sentinel reads camera <strong>metadata</strong> from each as soon as that department&rsquo;s
            own validation passes, and never holds stream URLs or VMS credentials. Footage is
            brokered per session, only for the units the owning department has said yes to.
          </p>
        </Card>
      </div>
    </>
  );
}
