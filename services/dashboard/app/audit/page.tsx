"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { LoadingPanel } from "@/components/Shell";
import {
  Card,
  EmptyState,
  FloatInput,
  FloatSelect,
  Notice,
  OutcomePill,
  PageHeader,
} from "@/components/ui";
import { api } from "@/lib/api";
import { humanise, ist, orDash } from "@/lib/format";
import type { AuditEntry } from "@/lib/types";

/** Every action the central API records. Kept in sync with audit_service.py. */
const ACTIONS = [
  "login",
  "login_failed",
  "installation_form_created",
  "installation_form_updated",
  "installation_form_submitted",
  "installation_request_suspended",
  "installation_request_decommissioned",
  "installation_register_viewed",
  "installation_request_viewed",
  "camera_metadata_synchronized",
  "camera_registry_viewed",
  "camera_details_viewed",
  "access_policy_viewed",
  "camera_health_viewed",
  "audit_viewed",
  "video_access_denied",
  "video_access_requested",
  "video_access_granted",
  "video_access_refused",
  "video_access_revoked",
  "video_session_opened",
  "video_stream_accessed",
];

export default function AuditPage() {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [action, setAction] = useState<string>("");
  const [outcome, setOutcome] = useState<string>("");
  const [query, setQuery] = useState("");

  const load = useCallback(async () => {
    try {
      setEntries(await api.audit({ limit: "500" }));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = setInterval(load, 15_000);
    return () => clearInterval(timer);
  }, [load]);

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return entries.filter((entry) => {
      if (action && entry.action !== action) return false;
      if (outcome && entry.outcome !== outcome) return false;
      if (needle) {
        const haystack = [
          entry.username,
          entry.resource_id ?? "",
          entry.department ?? "",
          entry.case_or_reason ?? "",
        ]
          .join(" ")
          .toLowerCase();
        if (!haystack.includes(needle)) return false;
      }
      return true;
    });
  }, [entries, action, outcome, query]);

  const denied = entries.filter((entry) => entry.action === "video_access_denied");

  if (loading) return <LoadingPanel label="Loading audit log" />;

  return (
    <>
      <PageHeader
        title="Audit log"
        subtitle="Every onboarding step, approval decision, metadata read and video-access attempt."
      />

      <div className="space-y-3">
        {error && <Notice tone="bad">{error}</Notice>}

        {denied.length > 0 && (
          <Notice tone="warn" title={`${denied.length} refused video-access attempt(s) recorded`}>
            Every refused footage request is recorded, with the reason. Most are routine
            &mdash; an operator reaching a camera another unit owns before asking for it. A
            cluster against one camera, or one account, is worth a look.
          </Notice>
        )}

        <div className="card flex flex-wrap items-end gap-3 px-3 py-2.5">
          <FloatInput
            label="Search"
            className="min-w-[16rem] flex-1"
              hint="User, resource ID, case ID or reason…"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
          />
          <FloatSelect
            label="Action"
            className="min-w-[14rem]"
              value={action}
              onChange={(event) => setAction(event.target.value)}
          >
              <option value="">All actions</option>
              {ACTIONS.map((value) => (
                <option key={value} value={value}>
                  {humanise(value)}
                </option>
              ))}
          </FloatSelect>
          <FloatSelect
            label="Outcome"
            className="min-w-[9rem]"
              value={outcome}
              onChange={(event) => setOutcome(event.target.value)}
          >
              <option value="">Any</option>
              <option value="success">success</option>
              <option value="partial">partial</option>
              <option value="denied">denied</option>
              <option value="error">error</option>
          </FloatSelect>
          <div className="ml-auto flex items-center gap-2">
            <span className="text-2xs text-ink-500">
              <span className="tabular font-medium text-ink-900">{visible.length}</span> of{" "}
              <span className="tabular font-medium text-ink-900">{entries.length}</span> entries
            </span>
            {(action || outcome || query) && (
              <button
                className="btn btn-sm"
                onClick={() => {
                  setAction("");
                  setOutcome("");
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
                  <th>Time (IST)</th>
                  <th>User</th>
                  <th>Role</th>
                  <th>Action</th>
                  <th>Resource</th>
                  <th>Department</th>
                  <th>Result</th>
                  <th>Case / reason</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((entry) => (
                  <tr key={entry.audit_id}>
                    <td className="mono whitespace-nowrap">{ist(entry.timestamp_utc)}</td>
                    <td className="whitespace-nowrap">{entry.username}</td>
                    <td className="whitespace-nowrap text-ink-500">
                      {entry.role.replace(/_/g, " ")}
                    </td>
                    <td className="whitespace-nowrap">{humanise(entry.action)}</td>
                    <td className="mono">
                      {entry.resource_type ? (
                        <>
                          <span className="text-ink-400">{entry.resource_type}:</span>{" "}
                          {orDash(entry.resource_id)}
                        </>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="whitespace-nowrap">{orDash(entry.department)}</td>
                    <td>
                      <OutcomePill outcome={entry.outcome} />
                    </td>
                    <td className="text-ink-500">{orDash(entry.case_or_reason)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {visible.length === 0 && <EmptyState message="No audit entries match the current filters." />}
        </Card>
      </div>
    </>
  );
}
