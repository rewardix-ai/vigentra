"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { LoadingPanel } from "@/components/Shell";
import { Card, EmptyState, HealthPill, Notice, PageHeader, Pill, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { download, toCSV } from "@/lib/csv";
import { calendarDate, ist } from "@/lib/format";
import type { GapAnalysis } from "@/lib/types";

/**
 * Gap-analysis report.
 *
 * Two axes an operations team actually looks at:
 *   - coverage — how many active cameras per district; which districts are thin
 *   - age      — cameras past their expected service life
 *
 * The report is scoped by the reader's own department scope (a Traffic account
 * only ever sees Traffic totals). The thresholds are query-adjustable so the
 * demo can show the same page telling different stories.
 */

function CoverageBar({ row }: { row: GapAnalysis["districts_covered"][number] }) {
  const total = Math.max(1, row.cameras);
  const seg = (part: number) => (part / total) * 100;
  return (
    <div className="flex h-2.5 overflow-hidden rounded-sm border border-line" title={`${row.cameras} cameras`}>
      <span style={{ width: `${seg(row.online)}%`, background: "var(--ok, #1a7f47)" }} />
      <span style={{ width: `${seg(row.degraded)}%`, background: "var(--warn, #a2680a)" }} />
      <span style={{ width: `${seg(row.offline)}%`, background: "var(--bad, #b3261e)" }} />
      <span style={{ width: `${seg(row.unavailable)}%`, background: "var(--idle, #7b858f)" }} />
    </div>
  );
}

export default function GapAnalysisPage() {
  const [report, setReport] = useState<GapAnalysis | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [minCameras, setMinCameras] = useState(3);
  const [ageingYears, setAgeingYears] = useState(5);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      setReport(
        await api.gapAnalysis({
          min_cameras_per_district: String(minCameras),
          ageing_years: String(ageingYears),
        }),
      );
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
      setLoading(false);
    }
  }, [minCameras, ageingYears]);

  useEffect(() => {
    void load();
  }, [load]);

  function exportReport() {
    if (!report) return;
    const districtHeader = [
      "district",
      "cameras",
      "online",
      "degraded",
      "offline",
      "unavailable",
      "is_thin",
    ];
    const ageingHeader = [
      "camera_id",
      "name",
      "owning_department",
      "district",
      "installation_date",
      "age_years",
      "health_status",
    ];
    const stamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
    const districtCsv = toCSV(
      report.districts_covered.map((row) => [
        row.district,
        row.cameras,
        row.online,
        row.degraded,
        row.offline,
        row.unavailable,
        row.is_thin ? "true" : "false",
      ]),
      districtHeader,
    );
    const ageingCsv = toCSV(
      report.ageing_cameras.map((row) => [
        row.camera_id,
        row.name,
        row.owning_department,
        row.district,
        row.installation_date ?? "",
        row.age_years ?? "",
        row.health_status,
      ]),
      ageingHeader,
    );
    download(`sentinel-gap-analysis-${stamp}-districts.csv`, districtCsv);
    download(`sentinel-gap-analysis-${stamp}-ageing.csv`, ageingCsv);
  }

  if (loading) return <LoadingPanel label="Compiling gap-analysis report" />;

  if (error || !report) {
    return (
      <>
        <PageHeader title="Gap analysis" />
        <Notice tone="bad">{error ?? "Report unavailable"}</Notice>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Gap analysis"
        subtitle={`Coverage and ageing infrastructure across the federated registry. Generated ${ist(
          report.generated_at,
        )}.`}
        actions={
          <>
            <button className="btn" onClick={exportReport} disabled={busy}>
              Export CSV
            </button>
            <button className="btn btn-primary" onClick={load} disabled={busy}>
              {busy && <Spinner />} Recompute
            </button>
          </>
        }
      />

      <div className="space-y-3">
        {/* Thresholds */}
        <Card title="Thresholds">
          <div className="grid gap-3 px-4 py-3 md:grid-cols-2">
            <label className="block">
              <span className="field-label">
                A district is thin when active cameras are below…
              </span>
              <input
                type="number"
                min={1}
                max={200}
                className="input mt-1 max-w-[8rem]"
                value={minCameras}
                onChange={(event) => setMinCameras(Math.max(1, Number(event.target.value) || 1))}
              />
            </label>
            <label className="block">
              <span className="field-label">
                Flag cameras older than this many years
              </span>
              <input
                type="number"
                min={1}
                max={40}
                className="input mt-1 max-w-[8rem]"
                value={ageingYears}
                onChange={(event) => setAgeingYears(Math.max(1, Number(event.target.value) || 1))}
              />
            </label>
          </div>
        </Card>

        {/* Totals */}
        <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-5">
          <StatTile label="Cameras in registry" value={report.totals.cameras} />
          <StatTile
            label="Active cameras"
            value={report.totals.active_cameras}
            hint="excluding withdrawn assets"
          />
          <StatTile label="Districts covered" value={report.totals.districts_covered} />
          <StatTile
            label="Thin districts"
            value={report.totals.thin_districts}
            tone={report.totals.thin_districts > 0 ? "warn" : "plain"}
            hint={`< ${report.thresholds.min_cameras_per_district} active`}
          />
          <StatTile
            label="Ageing cameras"
            value={report.totals.ageing_cameras}
            tone={report.totals.ageing_cameras > 0 ? "warn" : "plain"}
            hint={`≥ ${report.thresholds.ageing_years} yrs`}
          />
        </div>

        {report.departments_without_coverage.length > 0 && (
          <Notice tone="warn" title="Departments with no active cameras">
            {report.departments_without_coverage.join(", ")}
          </Notice>
        )}

        {/* District coverage */}
        <Card
          title="District coverage"
          action={
            <span className="text-2xs text-ink-500">
              Bar segments: <span className="mono">online</span> ·{" "}
              <span className="mono">degraded</span> · <span className="mono">offline</span> ·{" "}
              <span className="mono">unavailable</span>
            </span>
          }
        >
          {report.districts_covered.length === 0 ? (
            <EmptyState message="No cameras in the registry yet." />
          ) : (
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>District</th>
                    <th className="text-right">Cameras</th>
                    <th className="text-right">Online</th>
                    <th className="text-right">Degraded</th>
                    <th className="text-right">Offline</th>
                    <th className="text-right">Unavailable</th>
                    <th>Health mix</th>
                    <th>By department</th>
                    <th>Flag</th>
                  </tr>
                </thead>
                <tbody>
                  {report.districts_covered.map((row) => (
                    <tr key={row.district}>
                      <td className="font-medium">{row.district}</td>
                      <td className="tabular text-right">{row.cameras}</td>
                      <td className="tabular text-right">{row.online}</td>
                      <td className="tabular text-right">{row.degraded}</td>
                      <td className="tabular text-right">{row.offline}</td>
                      <td className="tabular text-right">{row.unavailable}</td>
                      <td className="min-w-[180px]">
                        <CoverageBar row={row} />
                      </td>
                      <td className="text-2xs text-ink-500">
                        {Object.entries(row.by_department)
                          .map(([dept, count]) => `${dept}: ${count}`)
                          .join(" · ") || "—"}
                      </td>
                      <td>{row.is_thin ? <Pill tone="warn">thin</Pill> : <Pill tone="ok">ok</Pill>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        {/* Ageing infrastructure */}
        <Card
          title="Ageing infrastructure"
          action={
            <span className="text-2xs text-ink-500">
              {report.ageing_cameras.length} camera(s) older than{" "}
              {report.thresholds.ageing_years} years
            </span>
          }
        >
          {report.ageing_cameras.length === 0 ? (
            <EmptyState
              message="No cameras exceed the ageing threshold."
              hint="Lower the threshold to inspect younger assets."
            />
          ) : (
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Camera</th>
                    <th>Camera ID</th>
                    <th>Department</th>
                    <th>District</th>
                    <th>Installed</th>
                    <th className="text-right">Age (yrs)</th>
                    <th>Health</th>
                  </tr>
                </thead>
                <tbody>
                  {report.ageing_cameras.map((row) => (
                    <tr key={row.camera_id}>
                      <td>
                        <Link
                          className="font-medium text-brand-600 hover:underline"
                          href={`/registry/${encodeURIComponent(row.camera_id)}`}
                        >
                          {row.name}
                        </Link>
                      </td>
                      <td className="mono">{row.camera_id}</td>
                      <td>{row.owning_department}</td>
                      <td>{row.district}</td>
                      <td className="text-ink-500">{calendarDate(row.installation_date)}</td>
                      <td className="tabular text-right">{row.age_years?.toFixed(1) ?? "—"}</td>
                      <td>
                        <HealthPill status={row.health_status} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <p className="text-2xs leading-relaxed text-ink-400">
          Thresholds are advisory — adjust them for your own coverage standard. The report is
          scoped by your role: a departmental account sees only its own department, a statewide
          account sees every federated department.
        </p>
      </div>
    </>
  );
}

function StatTile({
  label,
  value,
  hint,
  tone = "plain",
}: {
  label: string;
  value: number | string;
  hint?: string;
  tone?: "plain" | "warn" | "bad";
}) {
  const colour = {
    plain: "text-ink-900",
    warn: "text-warn",
    bad: "text-bad",
  }[tone];
  return (
    <div className="card px-4 py-3">
      <div className="field-label">{label}</div>
      <div className={`tabular mt-1 text-[26px] font-semibold leading-none ${colour}`}>{value}</div>
      {hint && <div className="mt-1.5 text-2xs text-ink-500">{hint}</div>}
    </div>
  );
}
