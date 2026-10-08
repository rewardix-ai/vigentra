"use client";

import { useState } from "react";

import { api } from "@/lib/api";
import type { ChallanIssued, ChallanPreview, Incident } from "@/lib/types";

/**
 * Confirm an incident as an offence and issue the e-challan.
 *
 * The operator types the plate they read on the evidence (the system's own reading is not used: a
 * person confirms the vehicle). "Find owner" looks it up (audited) and shows the owner, the masked
 * mobile, the section, fine and the SMS; "Issue challan & send SMS" issues it and confirms the
 * incident. Owners come from the DEMO stand-in for VAHAN and the SMS is simulated until a gateway
 * is configured; both are said on screen.
 */
export function ChallanDialog({
  incident,
  snapshotUrl,
  onClose,
  onIssued,
}: {
  incident: Incident;
  snapshotUrl: string | null;
  onClose: () => void;
  onIssued: (challan: ChallanIssued) => void;
}) {
  const [plate, setPlate] = useState("");
  const [preview, setPreview] = useState<ChallanPreview | null>(null);
  const [issued, setIssued] = useState<ChallanIssued | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const lookup = () =>
    run(async () => {
      setPreview(await api.challanLookup(incident.incident_id, plate));
    });
  const issue = () =>
    run(async () => {
      const c = await api.issueChallan(incident.incident_id, plate);
      setIssued(c);
      onIssued(c);
    });

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      role="dialog"
      aria-modal="true"
      aria-label="Confirm and issue e-challan"
      onClick={(e) => e.target === e.currentTarget && !busy && onClose()}
    >
      <div className="max-h-full w-full max-w-lg overflow-y-auto rounded-lg bg-white shadow-xl">
        <div className="border-b border-ink-200 px-4 py-3">
          <div className="text-[13px] font-semibold text-ink-900">Confirm offence and issue e-challan</div>
          <div className="text-2xs text-ink-500">
            {incident.camera_name ?? incident.camera_id} · {incident.reason}
          </div>
        </div>

        <div className="space-y-3 px-4 py-3">
          {snapshotUrl && (
            // eslint-disable-next-line @next/next/no-img-element
            <img alt="Evidence frame" className="w-full rounded border border-ink-200" src={snapshotUrl} />
          )}

          {!issued && (
            <div>
              <label className="mb-1 block text-2xs font-medium text-ink-700" htmlFor="challan-plate">
                Number plate, as you read it on the evidence
              </label>
              <div className="flex gap-2">
                <input
                  id="challan-plate"
                  className="mono flex-1 rounded border border-ink-300 px-2 py-1.5 text-[13px] uppercase"
                  placeholder="GJ01AB1234"
                  value={plate}
                  autoFocus
                  onChange={(e) => {
                    setPlate(e.target.value.toUpperCase());
                    setPreview(null);
                  }}
                  onKeyDown={(e) => e.key === "Enter" && plate.trim().length >= 4 && !busy && lookup()}
                />
                <button className="btn btn-sm" disabled={busy || plate.trim().length < 4} onClick={lookup}>
                  Find owner
                </button>
              </div>
            </div>
          )}

          {error && <div className="rounded bg-red-50 px-2 py-1.5 text-2xs text-red-700">{error}</div>}

          {(issued ?? preview) && (() => {
            const p = (issued ?? preview) as ChallanPreview;
            return (
              <div className="space-y-2 rounded border border-ink-200 bg-ink-50 px-3 py-2 text-2xs text-ink-700">
                {p.demo && (
                  <div className="rounded bg-amber-100 px-2 py-1 text-[11px] text-amber-900">
                    DEMO registry: fictional owner, not VAHAN. SMS is{" "}
                    {p.sms_provider === "simulated" ? "simulated (no gateway configured)" : `sent via ${p.sms_provider}`}.
                  </div>
                )}
                <div className="grid grid-cols-[110px_1fr] gap-x-2 gap-y-1">
                  <span className="text-ink-500">Vehicle</span>
                  <span className="mono font-semibold">{p.plate}</span>
                  <span className="text-ink-500">Owner</span>
                  <span>{p.owner_name}</span>
                  <span className="text-ink-500">Mobile</span>
                  <span className="mono">{p.mobile_masked}</span>
                  <span className="text-ink-500">Offence</span>
                  <span>{p.offence}</span>
                  <span className="text-ink-500">Section</span>
                  <span>{p.section}</span>
                  <span className="text-ink-500">Fine</span>
                  <span>
                    Rs {p.fine_rupees.toLocaleString("en-IN")} {p.fine_note}
                  </span>
                </div>
                <div className="text-[10px] text-ink-400">{p.verify}</div>
                <div>
                  <div className="mb-0.5 text-ink-500">SMS</div>
                  <div className="rounded border border-ink-200 bg-white px-2 py-1.5 leading-relaxed">{p.sms_text}</div>
                </div>
              </div>
            );
          })()}

          {issued && (
            <div className="rounded bg-emerald-50 px-2 py-1.5 text-2xs text-emerald-800">
              Challan <span className="mono font-semibold">{issued.challan_no}</span> issued; SMS{" "}
              {issued.sms_status === "SIMULATED" ? "recorded (simulated, not sent)" : issued.sms_status.toLowerCase()}.
              Incident confirmed.
            </div>
          )}
        </div>

        <div className="flex justify-end gap-2 border-t border-ink-200 px-4 py-3">
          <button className="btn btn-sm" disabled={busy} onClick={onClose}>
            {issued ? "Close" : "Cancel"}
          </button>
          {!issued && (
            <button className="btn btn-sm btn-primary" disabled={busy || !preview} onClick={issue}>
              Issue challan &amp; send SMS
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
