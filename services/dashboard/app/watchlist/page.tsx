"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Plus } from "lucide-react";

import {
  Card,
  FloatInput,
  FloatSelect,
  Notice,
  PageHeader,
  Pill,
  Spinner,
} from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { CATEGORY_TONE } from "@/lib/constants";
import { ist, relative } from "@/lib/format";
import type { WatchCategory, WatchlistEntry } from "@/lib/types";

/**
 * The vehicles this network has been asked to watch for.
 *
 * A watchlist entry is a standing instruction: from the moment it is saved,
 * every camera in scope flags that vehicle every time it is seen, statewide,
 * until someone stands the entry down. That is why the form asks for a reason
 * and an end date, why entries are deactivated rather than deleted, and why
 * this page shows who added each one.
 *
 * The alert count beside each entry is worth reading. An entry firing
 * constantly is usually a plate that sits one confusion-pair away from
 * something common — a tuning problem, not forty stolen cars.
 */

const CATEGORIES: { value: WatchCategory; label: string; hint: string }[] = [
  { value: "stolen", label: "Stolen", hint: "Reported stolen — an FIR exists" },
  { value: "wanted", label: "Wanted", hint: "Associated with a wanted person" },
  { value: "blacklist", label: "Blacklisted", hint: "Barred from an area or facility" },
  { value: "missing", label: "Missing", hint: "Linked to a missing-person case" },
  { value: "suspect", label: "Suspect", hint: "Under investigation — lowest confidence" },
];

export default function WatchlistPage() {
  const [rows, setRows] = useState<WatchlistEntry[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [showInactive, setShowInactive] = useState(false);

  const [plate, setPlate] = useState("");
  const [category, setCategory] = useState<WatchCategory>("stolen");
  const [reason, setReason] = useState("");
  const [caseReference, setCaseReference] = useState("");
  const [expiresAt, setExpiresAt] = useState("");

  const [standingDown, setStandingDown] = useState<string | null>(null);
  const [standDownReason, setStandDownReason] = useState("");

  const load = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      setRows(await api.watchlist({ active_only: showInactive ? "false" : "true" }));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, [showInactive]);

  useEffect(() => {
    void load();
  }, [load]);

  const add = async () => {
    setBusy(true);
    setError(null);
    setSaved(null);
    try {
      const entry = await api.addWatchlistEntry({
        plate: plate.trim().toUpperCase(),
        category,
        reason: reason.trim(),
        case_reference: caseReference.trim() || undefined,
        expires_at: expiresAt ? new Date(expiresAt).toISOString() : undefined,
      });
      setSaved(`${entry.plate} added — every camera in scope now flags it.`);
      setPlate("");
      setReason("");
      setCaseReference("");
      setExpiresAt("");
      await load();
    } catch (err) {
      // The API refuses a plate that is not shaped like an Indian
      // registration, because a string the matcher can never match is a typo
      // rather than a watchlist entry. Say that, rather than "422".
      setError(
        err instanceof ApiError && err.status === 422
          ? `${err.message} Check the registration and try again.`
          : err instanceof Error
            ? err.message
            : String(err),
      );
      setBusy(false);
    }
  };

  const standDown = async (entry: WatchlistEntry) => {
    setBusy(true);
    try {
      await api.deactivateWatchlistEntry(entry.entry_id, standDownReason.trim());
      setStandingDown(null);
      setStandDownReason("");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  };

  const ready = plate.trim().length >= 6 && reason.trim().length >= 8;

  return (
    <>
      <PageHeader
        title="Watchlist"
        subtitle="Registration numbers this network flags on sight."
      />

      <div className="space-y-3">
        {error && <Notice tone="bad">{error}</Notice>}
        {saved && <Notice tone="ok">{saved}</Notice>}

        <Notice tone="warn" title="An entry here changes what the whole network does">
          From the moment it is saved, every camera in scope flags this vehicle every time it is
          seen, and each hit reaches an operator as an{" "}
          <Link className="link" href="/alerts">
            alert
          </Link>
          . Adding an entry is recorded against your account with the reason you give. Set an end
          date — an entry with no end date is one nobody ever revisits.
        </Notice>

        <Card title="Add a vehicle">
          <div className="space-y-3 px-3 py-3">
            <div className="flex flex-wrap items-end gap-3">
              <FloatInput
                label="Registration number"
                inputClassName="mono"
                value={plate}
                onChange={(event) => setPlate(event.target.value.toUpperCase())}
                hint="e.g. GJ01AB1234"
              />

              <div>
                <FloatSelect
                  label="Category"
                  value={category}
                  onChange={(event) => setCategory(event.target.value as WatchCategory)}
                >
                  {CATEGORIES.map((item) => (
                    <option key={item.value} value={item.value} title={item.hint}>
                      {item.label}
                    </option>
                  ))}
                </FloatSelect>
                <span className="mt-1 block text-2xs text-ink-500">
                  {CATEGORIES.find((item) => item.value === category)?.hint}
                </span>
              </div>

              <FloatInput
                label="Case reference (optional)"
                  value={caseReference}
                  onChange={(event) => setCaseReference(event.target.value)}
                  hint="FIR 118/2026"
              />

              <FloatInput
                label="Stops matching on"
                  type="date"
                  value={expiresAt}
                  onChange={(event) => setExpiresAt(event.target.value)}
              />
            </div>

            <FloatInput
              label="Why is this vehicle being watched? (required)"
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                hint="Reported stolen from Sarkhej on 24 Aug 2026, FIR 118/2026"
            />

            <button className="btn btn-primary" onClick={() => void add()} disabled={busy || !ready}>
              {busy ? <Spinner /> : <Plus className="h-3.5 w-3.5" strokeWidth={2} aria-hidden />} Add to watchlist
            </button>
          </div>
        </Card>

        <label className="flex items-center gap-2 text-[13px]">
          <input
            type="checkbox"
            checked={showInactive}
            onChange={(event) => setShowInactive(event.target.checked)}
          />
          Include entries that have been stood down
        </label>

        {rows === null ? (
          <Card title="Loading">
            <div className="px-4 py-6 text-[13px] text-ink-500">
              <Spinner /> Reading the watchlist…
            </div>
          </Card>
        ) : rows.length === 0 ? (
          <Card title="Nothing is being watched">
            <div className="px-4 py-6 text-[13px] text-ink-500">
              The watchlist is empty, so no plate read anywhere on this network will raise an
              alert. Add a vehicle above.
            </div>
          </Card>
        ) : (
          <Card title="Watched vehicles">
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Plate</th>
                    <th>Category</th>
                    <th>Reason</th>
                    <th>Case</th>
                    <th>Added by</th>
                    <th>Expires</th>
                    <th>Alerts</th>
                    <th>Status</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.entry_id} className={row.active ? "" : "opacity-60"}>
                      <td className="mono font-semibold">{row.plate}</td>
                      <td>
                        <Pill tone={CATEGORY_TONE[row.category] ?? "idle"}>{row.category}</Pill>
                      </td>
                      <td className="max-w-[22rem] text-2xs">{row.reason}</td>
                      <td className="text-2xs">{row.case_reference ?? "—"}</td>
                      <td className="text-2xs">
                        <div>{row.added_by}</div>
                        {row.created_at && (
                          <div className="text-ink-500">{relative(row.created_at)}</div>
                        )}
                      </td>
                      <td className="whitespace-nowrap text-2xs">
                        {row.expires_at ? (
                          ist(row.expires_at)
                        ) : (
                          <span className="text-warn" title="Nothing will stand this entry down.">
                            no end date
                          </span>
                        )}
                      </td>
                      <td className="tabular">
                        {row.alert_count > 0 ? (
                          <Link className="link" href="/alerts">
                            {row.alert_count}
                          </Link>
                        ) : (
                          "0"
                        )}
                      </td>
                      <td>
                        {row.active ? (
                          <Pill tone="ok">active</Pill>
                        ) : (
                          <>
                            <Pill tone="idle">stood down</Pill>
                            <div className="text-2xs text-ink-500">by {row.deactivated_by}</div>
                          </>
                        )}
                      </td>
                      <td className="whitespace-nowrap">
                        {row.active && (
                          <div className="flex flex-col gap-1">
                            <button
                              className="btn btn-sm"
                              onClick={() =>
                                setStandingDown(
                                  standingDown === row.entry_id ? null : row.entry_id,
                                )
                              }
                              disabled={busy}
                            >
                              Stand down
                            </button>
                            {standingDown === row.entry_id && (
                              <div className="flex gap-1">
                                <FloatInput
                                  inputClassName="text-2xs"
                                  label="Why? e.g. vehicle recovered"
                                  value={standDownReason}
                                  onChange={(event) => setStandDownReason(event.target.value)}
                                />
                                <button
                                  className="btn btn-sm btn-primary"
                                  disabled={busy || standDownReason.trim().length < 4}
                                  onClick={() => void standDown(row)}
                                >
                                  Save
                                </button>
                              </div>
                            )}
                          </div>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        )}
      </div>
    </>
  );
}
