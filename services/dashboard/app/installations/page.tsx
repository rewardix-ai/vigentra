"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";

import { LoadingPanel } from "@/components/Shell";
import {
  Card,
  DepartmentTag,
  EmptyState,
  FloatInput,
  FloatSelect,
  Notice,
  PageHeader,
  RequestStatusPill,
} from "@/components/ui";
import { api } from "@/lib/api";
import { ist, orDash, relative } from "@/lib/format";
import type { InstallationRequest, Operator, RequestStatus } from "@/lib/types";

const STATUSES: RequestStatus[] = [
  "DRAFT",
  "SUBMITTED",
  "VALIDATION_FAILED",
  "REGISTERED",
  "SYNCHRONIZED",
  "SUSPENDED",
  "DECOMMISSIONED",
];

function RequestList() {
  const params = useSearchParams();
  const [records, setRecords] = useState<InstallationRequest[]>([]);
  const [operator, setOperator] = useState<Operator | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [status, setStatus] = useState<string>(params.get("status") ?? "");
  const [query, setQuery] = useState("");

  const load = useCallback(async () => {
    try {
      setRecords(await api.installationRequests());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    api.me().then(setOperator).catch(() => undefined);
    const timer = setInterval(load, 20_000);
    return () => clearInterval(timer);
  }, [load]);

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return records.filter((record) => {
      if (status && record.status !== status) return false;
      if (
        needle &&
        !(record.camera_name ?? "").toLowerCase().includes(needle) &&
        !record.request_id.toLowerCase().includes(needle) &&
        !(record.external_camera_id ?? "").toLowerCase().includes(needle)
      ) {
        return false;
      }
      return true;
    });
  }, [records, status, query]);

  const permissions = operator?.permissions ?? [];
  const canCreate = permissions.includes("installation:create");

  const counts = useMemo(() => {
    const tally: Record<string, number> = {};
    for (const record of records) tally[record.status] = (tally[record.status] ?? 0) + 1;
    return tally;
  }, [records]);

  if (loading) return <LoadingPanel label="Loading installation register" />;

  return (
    <>
      <PageHeader
        title="Installation requests"
        subtitle="CCTV installation records from each department."
      />

      <div className="space-y-3">
        {error && <Notice tone="bad">{error}</Notice>}

        {(counts.VALIDATION_FAILED ?? 0) > 0 && (
          <Notice
            tone="bad"
            title={`${counts.VALIDATION_FAILED} form(s) returned by validation`}
          >
            These are the only records still waiting on someone. Open one to see
            what the department system objected to, correct it, and submit again
            &mdash; it registers as soon as the fields pass.
          </Notice>
        )}

        {(counts.DRAFT ?? 0) > 0 && (
          <Notice tone="idle" title={`${counts.DRAFT} draft(s) not yet submitted`}>
            A draft is invisible outside your unit. Submitting it runs your
            department&rsquo;s validation and, if that passes, puts the camera in
            the central registry straight away.
          </Notice>
        )}

        {/* Filters */}
        <div className="card flex flex-wrap items-end gap-3 px-3 py-2.5">
          <FloatInput
            label="Search"
            className="min-w-[14rem] flex-1"
              hint="Request ID, camera name or camera ID…"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
          />
          <FloatSelect
            label="Status"
            className="min-w-[12rem]"
              value={status}
              onChange={(event) => setStatus(event.target.value)}
          >
              <option value="">All statuses</option>
              {STATUSES.map((value) => (
                <option key={value} value={value}>
                  {value.replace(/_/g, " ")} {counts[value] ? `(${counts[value]})` : ""}
                </option>
              ))}
          </FloatSelect>
          <div className="ml-auto flex items-center gap-2">
            <span className="text-2xs text-ink-500">
              <span className="tabular font-medium text-ink-900">{visible.length}</span> of{" "}
              <span className="tabular font-medium text-ink-900">{records.length}</span> records
            </span>
            {(status || query) && (
              <button
                className="btn btn-sm"
                onClick={() => {
                  setStatus("");
                  setQuery("");
                }}
              >
                Clear
              </button>
            )}
          </div>
        </div>

        <Card>
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Request ID</th>
                  <th>Camera name</th>
                  <th>Department</th>
                  <th>Submitted by</th>
                  <th>Created</th>
                  <th>Status</th>
                  <th>Last updated</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((record) => (
                  <tr key={record.request_id}>
                    <td className="mono whitespace-nowrap">{record.request_id}</td>
                    <td>
                      <Link
                        href={`/installations/${encodeURIComponent(record.request_id)}`}
                        className="font-medium text-brand-600 hover:underline"
                      >
                        {orDash(record.camera_name)}
                      </Link>
                      <div className="mono text-ink-400">{orDash(record.external_camera_id)}</div>
                    </td>
                    <td>
                      <DepartmentTag department={record.owning_department} />
                    </td>
                    <td className="text-ink-500">
                      {orDash(record.submitted_by ?? record.created_by)}
                    </td>
                    <td className="whitespace-nowrap text-ink-500" title={ist(record.created_at)}>
                      {relative(record.created_at)}
                    </td>
                    <td>
                      <RequestStatusPill status={record.status} />
                      {record.validation_errors.length > 0 && (
                        <div className="mt-1 text-2xs text-bad">
                          {record.validation_errors.length} validation issue(s)
                        </div>
                      )}
                    </td>
                    <td className="whitespace-nowrap text-ink-500" title={ist(record.updated_at)}>
                      {relative(record.updated_at)}
                    </td>
                    <td>
                      <Link
                        className="btn btn-sm"
                        href={`/installations/${encodeURIComponent(record.request_id)}`}
                      >
                        Open
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {visible.length === 0 && (
            <EmptyState
              message="No installation records match the current filters."
              hint={canCreate ? "Raise one from “New CCTV installation” in the sidebar." : undefined}
            />
          )}
        </Card>

        <p className="text-2xs leading-relaxed text-ink-400">
          These records live in each department&rsquo;s own system. Vigentra mirrors them so the
          pipeline is visible and auditable centrally, and publishes camera metadata to the registry
          as soon as a form passes its own department&rsquo;s validation &mdash; there is no approval
          queue. Footage is the part that still needs a decision: other units must request it, and
          the owning unit answers.
        </p>
      </div>
    </>
  );
}

export default function InstallationsPage() {
  return (
    <Suspense fallback={<LoadingPanel label="Loading installation register" />}>
      <RequestList />
    </Suspense>
  );
}
