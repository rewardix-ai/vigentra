"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";

import { LoadingPanel } from "@/components/Shell";
import {
  Card,
  DepartmentTag,
  EmptyState,
  GrantStatusPill,
  Notice,
  PageHeader,
  Spinner,
} from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { ist, orDash } from "@/lib/format";
import type { Operator, VideoAccessRequest } from "@/lib/types";

/**
 * Two queues, one page.
 *
 * Camera metadata federates on its own - every unit's cameras are already in
 * the registry. Footage does not. This page is the conversation that stands in
 * for the approval stage that used to gate metadata: another unit asks to
 * watch one of your cameras, and someone in your unit answers.
 */

function relative(iso: string | null): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const hours = Math.round((then - Date.now()) / 3_600_000);
  if (hours <= 0) return "expired";
  if (hours < 48) return `${hours}h left`;
  return `${Math.round(hours / 24)}d left`;
}

function RequestRow({
  record,
  role,
  busy,
  onDecide,
  onRevoke,
}: {
  record: VideoAccessRequest;
  role: "owner" | "requester";
  busy: boolean;
  onDecide: (id: string, verdict: "grant" | "deny") => void;
  onRevoke: (id: string) => void;
}) {
  return (
    <tr>
      <td className="whitespace-nowrap">
        <Link className="font-medium text-brand-600 hover:underline" href={`/registry/${encodeURIComponent(record.camera_id)}`}>
          {record.camera_name ?? record.camera_id}
        </Link>
        <div className="text-2xs text-ink-500">{record.camera_id}</div>
      </td>
      <td>
        {role === "owner" ? (
          <>
            <div>{record.requested_by}</div>
            <div className="text-2xs text-ink-500">{record.requester_department}</div>
          </>
        ) : (
          <DepartmentTag department={record.owning_department} />
        )}
      </td>
      <td className="max-w-[22rem]">
        <div className="break-words">{record.reason}</div>
        {record.case_id && <div className="text-2xs text-ink-500">Case {record.case_id}</div>}
      </td>
      <td className="whitespace-nowrap text-2xs">{record.allowed_modes.join(" + ") || "—"}</td>
      <td className="whitespace-nowrap">
        <GrantStatusPill status={record.status} />
        {record.status === "granted" && (
          <div className="text-2xs text-ink-500">{relative(record.expires_at)}</div>
        )}
        {record.decision_note && (
          <div className="text-2xs text-ink-500">{record.decision_note}</div>
        )}
      </td>
      <td className="whitespace-nowrap text-2xs text-ink-500">
        {ist(record.requested_at)}
        {record.decided_by && (
          <div>
            {record.status} by {orDash(record.decided_by)}
          </div>
        )}
      </td>
      <td className="whitespace-nowrap text-right">
        <div className="flex justify-end gap-1.5">
          {role === "owner" && record.status === "requested" && (
            <>
              <button
                className="btn btn-primary"
                disabled={busy}
                onClick={() => onDecide(record.grant_id, "grant")}
              >
                {busy && <Spinner />} Grant
              </button>
              <button
                className="btn btn-danger"
                disabled={busy}
                onClick={() => onDecide(record.grant_id, "deny")}
              >
                Deny
              </button>
            </>
          )}
          {record.status === "granted" && (
            <button className="btn" disabled={busy} onClick={() => onRevoke(record.grant_id)}>
              {role === "owner" ? "Revoke" : "Withdraw"}
            </button>
          )}
          {role === "requester" && record.status === "requested" && (
            <button className="btn" disabled={busy} onClick={() => onRevoke(record.grant_id)}>
              Withdraw
            </button>
          )}
        </div>
      </td>
    </tr>
  );
}

export default function AccessRequestsPage() {
  const [records, setRecords] = useState<VideoAccessRequest[]>([]);
  const [operator, setOperator] = useState<Operator | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ tone: "ok" | "bad"; text: string } | null>(null);

  const load = useCallback(async () => {
    try {
      const [rows, me] = await Promise.all([api.listAccessRequests(), api.me()]);
      setRecords(rows);
      setOperator(me);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // A request is "incoming" when this account's unit owns the camera, and
  // "outgoing" when this account raised it. An account can legitimately have
  // both, so the split is per row rather than per page.
  const { incoming, outgoing } = useMemo(() => {
    const mine = operator?.username;
    const dept = operator?.department;
    const inc: VideoAccessRequest[] = [];
    const out: VideoAccessRequest[] = [];
    for (const record of records) {
      if (record.requested_by === mine) out.push(record);
      else if (dept === "*" || record.owning_department === dept) inc.push(record);
    }
    return { incoming: inc, outgoing: out };
  }, [records, operator]);

  const canDecide = (operator?.permissions ?? []).includes("video:grant");

  async function act(fn: () => Promise<unknown>, done: string) {
    setBusy(true);
    setNotice(null);
    try {
      await fn();
      await load();
      setNotice({ tone: "ok", text: done });
    } catch (err) {
      const message =
        err instanceof ApiError && typeof err.detail === "string" ? err.detail : String(err);
      setNotice({ tone: "bad", text: message });
    } finally {
      setBusy(false);
    }
  }

  const onDecide = (id: string, verdict: "grant" | "deny") =>
    act(
      () => api.decideVideoAccess(id, verdict),
      verdict === "grant"
        ? "Access granted. It expires on its own; you can revoke it sooner."
        : "Request denied. The requesting unit can see your answer.",
    );

  const onRevoke = (id: string) => act(() => api.revokeVideoAccess(id), "Access ended.");

  if (loading) return <LoadingPanel label="Loading video access requests" />;

  const pending = incoming.filter((record) => record.status === "requested").length;

  return (
    <>
      <PageHeader
        title="Video access requests"
        subtitle="Camera records federate automatically. Footage does not — another unit has to ask, and your unit answers."
      />

      <div className="space-y-3">
        {error && <Notice tone="bad">{error}</Notice>}
        {notice && <Notice tone={notice.tone}>{notice.text}</Notice>}

        {pending > 0 && canDecide && (
          <Notice tone="warn" title={`${pending} request(s) waiting on your unit`}>
            Granting one lets that named officer open short, watermarked, audited sessions on that
            camera until the grant expires. It does not give their whole department access, and you
            can revoke it at any time.
          </Notice>
        )}

        <Card title="Requests for your unit&rsquo;s cameras">
          {incoming.length === 0 ? (
            <EmptyState
              message="Nothing to decide."
              hint="Requests from other departments appear here."
            />
          ) : (
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Camera</th>
                    <th>Requested by</th>
                    <th>Reason</th>
                    <th>Modes</th>
                    <th>Status</th>
                    <th>Raised</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {incoming.map((record) => (
                    <RequestRow
                      key={record.grant_id}
                      record={record}
                      role="owner"
                      busy={busy}
                      onDecide={onDecide}
                      onRevoke={onRevoke}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <Card title="Your requests to other units">
          {outgoing.length === 0 ? (
            <EmptyState
              message="You have not asked for any footage."
              hint="Open a camera you do not own in the registry and use Request access."
            />
          ) : (
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Camera</th>
                    <th>Owner</th>
                    <th>Your reason</th>
                    <th>Modes</th>
                    <th>Status</th>
                    <th>Raised</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {outgoing.map((record) => (
                    <RequestRow
                      key={record.grant_id}
                      record={record}
                      role="requester"
                      busy={busy}
                      onDecide={onDecide}
                      onRevoke={onRevoke}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <p className="text-2xs leading-relaxed text-ink-400">
          A grant is personal, time-boxed and revocable: it names one officer, one camera and which
          of live and playback they may open. Every session opened under it is watermarked and
          recorded in the audit log, and revoking a grant stops any session already running at its
          next read.
        </p>
      </div>
    </>
  );
}
