"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api, ApiError } from "@/lib/api";
import type { VideoMode, VideoSession } from "@/lib/types";
import { Notice, Spinner } from "./ui";

/**
 * Authorised viewing, with the conditions on screen rather than in a policy
 * document.
 *
 * Live runs continuously. Recorded playback asks for a date and a from/to
 * time, and can be asked again as many times as the operator likes - there is
 * deliberately no quota here. What stops a request is the owning unit: it can
 * revoke the grant, suspend the camera, turn brokering off, or simply not
 * retain footage that far back. Each of those comes back as a specific reason
 * rather than a flat refusal.
 *
 * The `src` is this origin's proxy path. No department URL, ticket or
 * credential ever reaches the browser.
 */
export function VideoPlayer({
  cameraId,
  cameraName,
  mode,
  retentionDays,
  onClosed,
}: {
  cameraId: string;
  cameraName: string;
  mode: VideoMode;
  retentionDays?: number | null;
  onClosed?: () => void;
}) {
  const [session, setSession] = useState<VideoSession | null>(null);
  const [reason, setReason] = useState("");
  const [caseId, setCaseId] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [remaining, setRemaining] = useState<number>(0);
  // Deliberately state rather than a ref. The <video> is not in the tree on
  // the render that opens a session, so an effect keyed only on `session`
  // would run once against a null ref and never again - hls.js would never be
  // attached and the player would sit on a black frame having requested
  // nothing. As state, the element's arrival re-runs the effect.
  const [videoEl, setVideoEl] = useState<HTMLVideoElement | null>(null);

  // Live grid cameras arrive as HLS. Safari plays a playlist natively; every
  // other browser needs hls.js, which is loaded only when a session actually
  // turns out to be HLS so the MP4 path costs nothing.
  useEffect(() => {
    const video = videoEl;
    if (!session || !video || session.stream_protocol !== "hls") return;

    const src = api.streamUrl(session);
    if (video.canPlayType("application/vnd.apple.mpegurl")) {
      video.src = src;
      return;
    }

    let destroyed = false;
    let hls: { destroy: () => void } | null = null;

    void import("hls.js").then(({ default: Hls }) => {
      if (destroyed || !Hls.isSupported()) return;
      const instance = new Hls({
        // The grid is a low-latency feed and the session is short. Chasing the
        // live edge matters more than a deep buffer here.
        lowLatencyMode: true,
        backBufferLength: 30,
      });
      hls = instance;
      instance.on(Hls.Events.ERROR, (_e: unknown, data: { fatal?: boolean }) => {
        // Transient segment errors are normal on a live feed and hls.js
        // recovers from them on its own; only a fatal one is worth telling
        // the operator about.
        if (data?.fatal) {
          setError(
            "The live feed stopped. The session may have expired, or the owning unit revoked it.",
          );
        }
      });
      instance.loadSource(src);
      instance.attachMedia(video);
    });

    return () => {
      destroyed = true;
      hls?.destroy();
    };
  }, [session, videoEl]);

  // Tracked in a ref so unmount cleanup can reach it without re-running on
  // every state change.
  const openSessionId = useRef<string | null>(null);
  useEffect(() => {
    openSessionId.current = session?.session_id ?? null;
  }, [session]);

  // Leaving the page, or switching mode, must end the session rather than
  // leaving it open upstream until it times out.
  useEffect(
    () => () => {
      const id = openSessionId.current;
      if (id) void api.closeVideoSession(id).catch(() => undefined);
    },
    [],
  );

  /**
   * Say why the feed actually failed.
   *
   * A <video> error event carries no status and no body, so the handler used
   * to guess "expired or revoked" for every failure - including a department
   * system that simply did not deliver media, which sent an operator looking
   * at the audit trail for a revocation that never happened. Ask the proxy
   * directly and report what it says.
   */
  const explainFailure = useCallback(async (current: VideoSession) => {
    const fallback =
      "The stream stopped. The session may have expired or been revoked by the owning unit.";
    try {
      const probe = await fetch(api.streamUrl(current), {
        headers: { Range: "bytes=0-1" },
        cache: "no-store",
      });
      if (probe.ok) {
        setError(fallback);
        return;
      }
      const body = (await probe.json().catch(() => null)) as
        | { detail?: { message?: string } | string }
        | null;
      const detail = body?.detail;
      const message = typeof detail === "string" ? detail : detail?.message;
      setError(message ?? fallback);
    } catch {
      setError(fallback);
    }
  }, []);

  const isPlayback = mode === "playback";

  // Default to a five-minute window an hour ago: recent enough to be inside
  // any sane retention, past enough to be a real recording.
  const defaults = useMemo(() => {
    const end = new Date(Date.now() - 60 * 60 * 1000);
    const start = new Date(end.getTime() - 5 * 60 * 1000);
    return {
      date: localDate(start),
      from: localTime(start),
      to: localTime(end),
    };
  }, []);

  const [date, setDate] = useState(defaults.date);
  const [fromTime, setFromTime] = useState(defaults.from);
  const [toTime, setToTime] = useState(defaults.to);

  const earliestDate = useMemo(() => {
    if (!retentionDays) return undefined;
    return localDate(new Date(Date.now() - retentionDays * 86_400_000));
  }, [retentionDays]);

  useEffect(() => {
    if (!session) return;
    setRemaining(session.expires_in_seconds);
    const timer = setInterval(() => {
      setRemaining((value) => {
        if (value <= 1) {
          clearInterval(timer);
          return 0;
        }
        return value - 1;
      });
    }, 1000);
    return () => clearInterval(timer);
  }, [session]);

  const open = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const body: Parameters<typeof api.openVideoSession>[0] = {
        camera_id: cameraId,
        mode,
        reason: reason.trim(),
        password,
        case_id: caseId.trim() || undefined,
      };

      if (isPlayback) {
        const start = new Date(`${date}T${fromTime}`);
        const end = new Date(`${date}T${toTime}`);
        if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) {
          throw new Error("Enter a valid date and time range.");
        }
        if (end <= start) {
          throw new Error("The end time must be after the start time.");
        }
        if (start > new Date()) {
          throw new Error("That window is in the future. Footage only exists for the past.");
        }
        body.start_time_utc = start.toISOString();
        body.end_time_utc = end.toISOString();
      }

      const opened = await api.openVideoSession(body);
      // Held only for the length of the request.
      setPassword("");
      setRemaining(opened.expires_in_seconds);
      setSession(opened);
    } catch (err) {
      setError(describe(err));
    } finally {
      setBusy(false);
    }
  }, [cameraId, mode, reason, caseId, password, isPlayback, date, fromTime, toTime]);

  const endSession = useCallback(
    async (keepForm: boolean) => {
      const current = session;
      setSession(null);
      setRemaining(0);
      if (current) {
        try {
          await api.closeVideoSession(current.session_id);
        } catch {
          // It expires on its own; failing to end it early is not worth an
          // error in the operator's face.
        }
      }
      if (!keepForm) onClosed?.();
    },
    [session, onClosed],
  );

  // ---------------------------------------------------------------- request

  if (!session) {
    return (
      <div className="space-y-2 rounded border border-line bg-[#f7f8fa] p-3">
        <div className="text-[13px] font-semibold text-ink-900">
          {isPlayback ? "Request recorded footage" : "Open a live session"} · {cameraName}
        </div>

        {isPlayback && (
          <>
            <div className="flex flex-wrap gap-3">
              <label className="block">
                <span className="field-label">Date</span>
                <input
                  className="input mt-1"
                  type="date"
                  value={date}
                  min={earliestDate}
                  max={localDate(new Date())}
                  onChange={(event) => setDate(event.target.value)}
                />
              </label>
              <label className="block">
                <span className="field-label">From</span>
                <input
                  className="input mt-1"
                  type="time"
                  step={60}
                  value={fromTime}
                  onChange={(event) => setFromTime(event.target.value)}
                />
              </label>
              <label className="block">
                <span className="field-label">To</span>
                <input
                  className="input mt-1"
                  type="time"
                  step={60}
                  value={toTime}
                  onChange={(event) => setToTime(event.target.value)}
                />
              </label>
            </div>
            <p className="text-2xs text-ink-500">
              {retentionDays
                ? `${cameraName} keeps ${retentionDays} days of footage. Anything older is gone, not withheld.`
                : "Times are read in your own timezone and sent as UTC."}
            </p>
          </>
        )}

        <label className="block">
          <span className="field-label">
            Why are you viewing this? <span className="text-bad">*</span>
          </span>
          <textarea
            className="textarea mt-1"
            rows={2}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder={
              isPlayback
                ? "e.g. Reviewing the 21:40 collision reported on this approach"
                : "e.g. Live monitoring of the evening peak"
            }
          />
        </label>
        <label className="block max-w-xs">
          <span className="field-label">
            Confirm your password <span className="text-bad">*</span>
          </span>
          <input
            className="input mt-1"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            placeholder="Re-enter to open the camera"
          />
        </label>
        <label className="block max-w-xs">
          <span className="field-label">Case / FIR reference (optional)</span>
          <input
            className="input mt-1"
            value={caseId}
            onChange={(event) => setCaseId(event.target.value)}
            placeholder="FIR 214/2026"
          />
        </label>

        <p className="text-2xs text-ink-500">
          Your password is re-checked at this point: a signed-in tab left unattended must not be
          enough to open a camera. The session is short-lived, watermarked with your username, and
          written to the audit trail. The owning unit can end it at any time.
        </p>
        {error && <Notice tone="bad">{error}</Notice>}
        <button
          className="btn btn-primary"
          disabled={busy || reason.trim().length < 5 || password.length === 0}
          onClick={open}
        >
          {busy && <Spinner />} {isPlayback ? "Retrieve footage" : "Start live"}
        </button>
      </div>
    );
  }

  // ----------------------------------------------------------------- player

  const expired = remaining <= 0;
  const isHls = session.stream_protocol === "hls";
  const fragment =
    session.segment_start_seconds != null && session.segment_end_seconds != null
      ? `#t=${session.segment_start_seconds},${session.segment_end_seconds}`
      : "";

  return (
    <div className="space-y-2 rounded border border-line bg-white p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-[13px] font-semibold text-ink-900">
          {!isPlayback && !expired && (
            <span className="inline-flex items-center gap-1.5 rounded bg-bad-bg px-1.5 py-0.5 text-2xs font-semibold uppercase tracking-wide text-bad">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-bad" />
              Live
            </span>
          )}
          <span>
            {cameraName}
            {isPlayback && session.start_time_utc && (
              <span className="ml-1 font-normal text-ink-500">
                · {windowLabel(session.start_time_utc, session.end_time_utc)}
              </span>
            )}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <span className={`text-2xs tabular-nums ${expired ? "text-bad" : "text-ink-500"}`}>
            {expired ? "session expired" : `expires in ${formatCountdown(remaining)}`}
          </span>
          {isPlayback && (
            <button className="btn" onClick={() => endSession(true)}>
              New time range
            </button>
          )}
          <button className="btn" onClick={() => endSession(false)}>
            Stop
          </button>
        </div>
      </div>

      {expired ? (
        <Notice tone="warn" title="This session has ended">
          Sessions are deliberately short. Start another if you still need the footage — it will be
          audited separately.
        </Notice>
      ) : (
        <div className="relative overflow-hidden rounded border border-line bg-black">
          <video
            ref={setVideoEl}
            key={session.session_id}
            className="block max-h-[60vh] w-full"
            // HLS is attached by the effect above (hls.js, or natively on
            // Safari). Setting src here as well would start a second, competing
            // load of the same playlist.
            src={isHls ? undefined : `${api.streamUrl(session)}${fragment}`}
            controls
            autoPlay
            playsInline
            // Live is a continuous feed, so it does not stop at the end of the
            // buffer. Recorded footage does - it is a finite segment.
            loop={!isPlayback && !isHls}
            onError={() => void explainFailure(session)}
          />
          <div className="pointer-events-none absolute right-2 top-2 rounded bg-black/55 px-2 py-1 text-2xs text-white">
            {session.watermark}
          </div>
        </div>
      )}

      {error && <Notice tone="bad">{error}</Notice>}

      <dl className="grid gap-x-4 gap-y-1 text-2xs text-ink-500 sm:grid-cols-4">
        <div>
          <dt className="field-label">Custodian</dt>
          <dd>{session.department}</dd>
        </div>
        <div>
          <dt className="field-label">Mode</dt>
          <dd>{isPlayback ? "Recorded playback" : "Live feed"}</dd>
        </div>
        <div>
          <dt className="field-label">Audit entry</dt>
          <dd className="mono break-all">{session.audit_id ?? "—"}</dd>
        </div>
        <div>
          <dt className="field-label">Reason recorded</dt>
          <dd>{session.reason ?? "—"}</dd>
        </div>
      </dl>
    </div>
  );
}

/** `YYYY-MM-DD` in the viewer's own timezone, for a date input. */
function localDate(value: Date): string {
  const offset = value.getTimezoneOffset() * 60_000;
  return new Date(value.getTime() - offset).toISOString().slice(0, 10);
}

/** `HH:MM` in the viewer's own timezone, for a time input. */
function localTime(value: Date): string {
  const offset = value.getTimezoneOffset() * 60_000;
  return new Date(value.getTime() - offset).toISOString().slice(11, 16);
}

function windowLabel(startIso: string, endIso: string | null): string {
  const start = new Date(startIso);
  const end = endIso ? new Date(endIso) : null;
  const day = start.toLocaleDateString(undefined, { day: "2-digit", month: "short", year: "numeric" });
  const from = start.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  const to = end ? end.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }) : "";
  return end ? `${day}, ${from}–${to}` : `${day}, ${from}`;
}

function formatCountdown(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function describe(err: unknown): string {
  if (err instanceof ApiError) {
    const detail = err.detail as
      | { message?: string; state?: string; owning_department?: string }
      | string
      | undefined;
    if (typeof detail === "string") return detail;
    if (detail?.message) return detail.message;
  }
  return err instanceof Error ? err.message : String(err);
}
