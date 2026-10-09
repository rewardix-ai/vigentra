"use client";

import { useState } from "react";

import { api } from "@/lib/api";
import type { ChallanIssued, ChallanPreview, Incident } from "@/lib/types";
import { Notice } from "./ui";

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
      className="fixed inset-0 z-50 !mt-0 flex items-center justify-center bg-black/50 p-4"
      role="dialog"
      aria-modal="true"
      aria-label="Confirm and issue e-challan"
      onClick={(e) => e.target === e.currentTarget && !busy && onClose()}
    >
      {/* A dialog floats over the page rather than sitting on it, so it is one
          of the few surfaces here that carries a shadow. */}
      <div className="max-h-full w-full max-w-lg overflow-y-auto rounded-md bg-canvas shadow-overlay">
        <div className="border-b border-hairline-soft px-6 py-5">
          <div className="card-title">Confirm offence and issue e-challan</div>
          <div className="mt-1 text-caption text-muted">
            {incident.camera_name ?? incident.camera_id} · {incident.reason}
          </div>
        </div>

        <div className="space-y-3 px-6 py-5">
          {snapshotUrl && (
            // eslint-disable-next-line @next/next/no-img-element
            <img alt="Evidence frame" className="w-full rounded-sm" src={snapshotUrl} />
          )}

          {!issued && (
            <div>
              <label className="field-label mb-1.5 block" htmlFor="challan-plate">
                Number plate, as you read it on the evidence
              </label>
              <div className="flex gap-2">
                <input
                  id="challan-plate"
                  className="input flex-1 font-mono"
                  placeholder="GJ01AB1234"
                  value={plate}
                  autoFocus
                  onChange={(e) => {
                    setPlate(e.target.value.toUpperCase());
                    setPreview(null);
                  }}
                  onKeyDown={(e) => e.key === "Enter" && plate.trim().length >= 4 && !busy && lookup()}
                />
                <button className="btn h-11" disabled={busy || plate.trim().length < 4} onClick={lookup}>
                  Find owner
                </button>
              </div>
            </div>
          )}

          {error && <Notice tone="bad">{error}</Notice>}

          {(issued ?? preview) && (() => {
            const p = (issued ?? preview) as ChallanPreview;
            return (
              <div className="panel space-y-3 px-4 py-3 text-caption text-ink-soft">
                {p.demo && (
                  <div className="font-semibold text-warn">
                    Demo registry: fictional owner, not VAHAN. SMS is{" "}
                    {p.sms_provider === "simulated" ? "simulated (no gateway configured)" : `sent via ${p.sms_provider}`}.
                  </div>
                )}
                <div className="grid grid-cols-[7rem_1fr] gap-x-3 gap-y-1">
                  <span className="text-muted">Vehicle</span>
                  <span className="mono font-semibold">{p.plate}</span>
                  <span className="text-muted">Owner</span>
                  <span>{p.owner_name}</span>
                  <span className="text-muted">Mobile</span>
                  <span className="mono">{p.mobile_masked}</span>
                  <span className="text-muted">Offence</span>
                  <span>{p.offence}</span>
                  <span className="text-muted">Section</span>
                  <span>{p.section}</span>
                  <span className="text-muted">Fine</span>
                  <span>
                    Rs {p.fine_rupees.toLocaleString("en-IN")} {p.fine_note}
                  </span>
                </div>
                <div className="text-caption text-muted">{p.verify}</div>
                <div>
                  <div className="mb-1 text-muted">SMS</div>
                  <div className="rounded-sm bg-canvas px-3 py-2 leading-relaxed">{p.sms_text}</div>
                </div>
              </div>
            );
          })()}

          {issued && (
            <Notice tone="ok">
              Challan <span className="mono font-semibold">{issued.challan_no}</span> issued; SMS{" "}
              {issued.sms_status === "SIMULATED" ? "recorded (simulated, not sent)" : issued.sms_status.toLowerCase()}.
              Incident confirmed.
            </Notice>
          )}
        </div>

        <div className="flex justify-end gap-2 border-t border-hairline-soft px-6 py-4">
          <button className="btn" disabled={busy} onClick={onClose}>
            {issued ? "Close" : "Cancel"}
          </button>
          {!issued && (
            <button className="btn btn-primary" disabled={busy || !preview} onClick={issue}>
              Issue challan &amp; send SMS
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
