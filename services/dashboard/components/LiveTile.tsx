"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { api, ApiError } from "@/lib/api";
import { streamQueue, type Release } from "@/lib/streamQueue";
import type { Camera, VideoSession } from "@/lib/types";

/**
 * One camera on the live wall.
 *
 * A tile only opens a session when it is actually on screen, and gives it back
 * the moment it scrolls away. That is not an optimisation detail - the grid's
 * own guidance is "each connected client receives its own copy of the stream;
 * open only the cameras you are actively processing". Thirty permanent
 * sessions for a wall the operator is scrolling past would be exactly the
 * abuse that warns against, and each one is an audited access besides.
 *
 * Starting is queued rather than immediate; see lib/streamQueue. The slot is
 * held until the feed is playing or has failed, so the gateway sets up a few
 * streams at a time while every tile still ends up live.
 *
 * A tile that fails keeps trying on a backoff instead of settling into an
 * error message. A wall is left running unattended, and a camera that drops
 * for a minute should return to the wall on its own rather than when somebody
 * notices and reloads the page.
 */

/**
 * How long a feed may be starting before the slot is taken back.
 *
 * Without this, one camera whose manifest never arrives holds a slot forever
 * and the queue behind it stops moving - the exact stall this queue exists to
 * remove, with a smaller number.
 *
 * Generous, because it has to cover the whole start: the session POST, the
 * proxied manifest fetch and the first segment, every one of them a round trip
 * to a gateway on the public internet. It is a backstop for a feed that never
 * arrives, not a latency budget - a tile that starts slowly is still a tile
 * that works, and cutting it off produces a wall where nothing plays.
 */
const START_TIMEOUT_MS = 70_000;

/** Backoff between retries: 3s, 6s, 12s, 24s, then every 30s. */
function backoffMs(attempt: number): number {
  return Math.min(30_000, 3_000 * 2 ** Math.max(0, attempt - 1));
}

type Phase = "idle" | "queued" | "opening" | "live" | "waiting";

export function LiveTile({
  camera,
  reason,
  password,
  onOpenFull,
  compact = false,
  snapshot = false,
}: {
  camera: Camera;
  reason: string;
  /** Confirmed once for the wall; each tile still re-authenticates per session. */
  password: string;
  onOpenFull?: (cameraId: string) => void;
  /**
   * Snapshot mode: show the camera's latest still frame (proxied from the edge
   * worker over the fast RTSP path) instead of opening an HLS session. The
   * grid's HLS CDN cannot feed a browser wall, so this is how the wall shows
   * every camera at once. No session, no per-frame password: the API gates the
   * snapshot on the same live permission.
   */
  snapshot?: boolean;
  /**
   * Wall mode: the tile is pure video that fills the grid cell it is given,
   * with the camera's name as an overlay instead of a metadata block below.
   * Used when every camera has to fit on one screen at once.
   */
  compact?: boolean;
}) {
  const [session, setSession] = useState<VideoSession | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [visible, setVisible] = useState(false);
  const [phase, setPhase] = useState<Phase>("idle");
  const [attempt, setAttempt] = useState(0);
  const holderRef = useRef<HTMLDivElement | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const sessionIdRef = useRef<string | null>(null);
  const releaseRef = useRef<Release | null>(null);
  // Shared between the two effects on purpose.
  //
  // The deadline is armed where the session is opened and has to be cancelled
  // where the video reports itself playing, which is a different effect. Held
  // in the effect that arms it, it could never be cancelled - so it fired on
  // healthy tiles too, revoked a session that was streaming perfectly well,
  // and every segment after it came back 410. A wall of tiles that each died
  // twenty seconds after appearing looks exactly like footage that does not
  // work at all.
  const watchdogRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const watchable =
    camera.video_access === "live_and_playback" || camera.video_access === "live_only";

  useEffect(() => {
    sessionIdRef.current = session?.session_id ?? null;
  }, [session]);

  /** Hand the admission slot back; the next tile in the queue starts. */
  const releaseSlot = useCallback(() => {
    releaseRef.current?.();
    releaseRef.current = null;
  }, []);

  /** Disarm the start deadline. Called once the feed is genuinely playing. */
  const clearWatchdog = useCallback(() => {
    if (watchdogRef.current) clearTimeout(watchdogRef.current);
    watchdogRef.current = null;
  }, []);

  // Only tiles the operator can actually see hold a session.
  useEffect(() => {
    const node = holderRef.current;
    if (!node) return;
    const observer = new IntersectionObserver(
      ([entry]) => setVisible(entry.isIntersecting),
      { rootMargin: "200px" },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  const close = useCallback(async () => {
    const id = sessionIdRef.current;
    sessionIdRef.current = null;
    setSession(null);
    if (id) await api.closeVideoSession(id).catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!visible || !watchable) {
      releaseSlot();
      setPhase("idle");
      void close();
      return;
    }
    if (sessionIdRef.current) return;

    const controller = new AbortController();

    /** Give up on this attempt and schedule the next one. */
    const fail = (message: string) => {
      if (controller.signal.aborted) return;
      clearWatchdog();
      releaseSlot();
      setError(message);
      setPhase("waiting");
      void close();
      setTimeout(() => {
        if (!controller.signal.aborted) setAttempt((n) => n + 1);
      }, backoffMs(attempt + 1));
    };

    void (async () => {
      setPhase("queued");
      let release: Release;
      try {
        release = await streamQueue.acquire(controller.signal);
      } catch {
        return; // scrolled away or unmounted while queued
      }
      if (controller.signal.aborted) {
        release();
        return;
      }
      releaseRef.current = release;
      setPhase("opening");

      // The slot is not held past this even if the feed never produces a
      // frame, so one dead camera cannot block the rest of the wall. Cancelled
      // by the attach effect as soon as the video reports it is playing.
      watchdogRef.current = setTimeout(() => fail("Feed did not start"), START_TIMEOUT_MS);

      try {
        const opened = await api.openVideoSession({
          camera_id: camera.camera_id,
          mode: "live",
          reason,
          password,
        });
        if (controller.signal.aborted) {
          void api.closeVideoSession(opened.session_id).catch(() => undefined);
          releaseSlot();
          return;
        }
        setSession(opened);
        setError(null);
      } catch (err) {
        fail(describe(err));
      }
    })();

    return () => {
      controller.abort();
      clearWatchdog();
      releaseSlot();
    };
  }, [
    visible,
    watchable,
    camera.camera_id,
    reason,
    password,
    attempt,
    close,
    releaseSlot,
    clearWatchdog,
  ]);

  // Give the session and the slot back on unmount rather than leaving them to
  // time out.
  useEffect(
    () => () => {
      clearWatchdog();
      releaseSlot();
      void close();
    },
    [close, releaseSlot, clearWatchdog],
  );

  // Attach the feed. HLS needs hls.js outside Safari.
  useEffect(() => {
    const video = videoRef.current;
    if (!session || !video) return;

    const src = api.streamUrl(session);
    let destroyed = false;
    let hls: { destroy: () => void } | null = null;

    // Playing is what ends the start: the slot goes back to the queue here,
    // not when the session was granted. A granted session that never renders
    // is not a started feed.
    const onPlaying = () => {
      if (destroyed) return;
      // Order matters: disarm the deadline BEFORE anything else, because a
      // feed that has started is no longer a feed that failed to start.
      clearWatchdog();
      setPhase("live");
      setError(null);
      releaseSlot();
    };
    // Nothing listens for `stalled`. A live stream stalls briefly all the
    // time - a segment arrives late, the buffer drains - and the player
    // recovers on its own; hls.js raises a fatal error when it cannot. Before
    // the first frame, `stalled` is also exactly the condition the start
    // deadline is there to catch, so treating it as news would disarm the one
    // check that matters.
    video.addEventListener("playing", onPlaying);
    video.addEventListener("loadeddata", onPlaying);

    if (session.stream_protocol !== "hls" || video.canPlayType("application/vnd.apple.mpegurl")) {
      video.src = src;
    } else {
      void import("hls.js").then(({ default: Hls }) => {
        if (destroyed || !Hls.isSupported()) return;
        const instance = new Hls({
          lowLatencyMode: true,
          backBufferLength: 10,
          // The grid can take ~30s to serialise a camera's playlist on a cold
          // fetch (the broker caches and trims it, but the first viewer still
          // waits on the grid). The default 10s manifest timeout would give up
          // long before it arrives, so the tile went black on a feed that was
          // simply slow to start. One retry, generously spaced.
          manifestLoadingTimeOut: 45_000,
          manifestLoadingMaxRetry: 2,
          manifestLoadingRetryDelay: 2_000,
          levelLoadingTimeOut: 45_000,
        });
        hls = instance;
        instance.on(Hls.Events.ERROR, (_e: unknown, data: { fatal?: boolean }) => {
          if (!data?.fatal || destroyed) return;
          // A fatal hls.js error means this session is finished. Drop it and
          // let the retry path open a fresh one rather than leaving a dead
          // <video> on the wall.
          releaseSlot();
          setError("Feed stopped");
          setPhase("waiting");
          setTimeout(() => {
            if (!destroyed) setAttempt((n) => n + 1);
          }, backoffMs(1));
        });
        instance.loadSource(src);
        instance.attachMedia(video);
      });
    }

    return () => {
      destroyed = true;
      video.removeEventListener("playing", onPlaying);
      video.removeEventListener("loadeddata", onPlaying);
      hls?.destroy();
    };
  }, [session, releaseSlot, clearWatchdog]);

  const loc = camera.location;

  if (snapshot) {
    return (
      <SnapshotTile camera={camera} compact={compact} onOpenFull={onOpenFull} />
    );
  }

  return (
    <div
      ref={holderRef}
      className={
        compact
          ? "relative h-full min-h-0 w-full overflow-hidden bg-black"
          : "overflow-hidden rounded border border-line bg-white"
      }
    >
      <div className={compact ? "relative h-full w-full bg-black" : "relative aspect-video bg-black"}>
        {session ? (
          <>
            <video
              ref={videoRef}
              key={session.session_id}
              className={compact ? "h-full w-full object-contain" : "h-full w-full object-cover"}
              muted
              autoPlay
              playsInline
            />
            <span className="pointer-events-none absolute left-1.5 top-1.5 inline-flex items-center gap-1 rounded bg-black/60 px-1.5 py-0.5 text-2xs font-semibold uppercase tracking-wide text-white">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-bad" />
              Live
            </span>
            <span className="pointer-events-none absolute bottom-1.5 right-1.5 max-w-[90%] truncate rounded bg-black/55 px-1.5 py-0.5 text-[10px] text-white">
              {session.watermark}
            </span>
          </>
        ) : (
          <div className="flex h-full items-center justify-center px-3 text-center text-2xs text-ink-500">
            {!watchable
              ? camera.video_access_reason ?? "Not viewable by this account"
              : statusText(phase, visible, error, attempt)}
          </div>
        )}
      </div>

      {compact ? (
        <button
          className="absolute bottom-1 left-1 max-w-[70%] truncate rounded bg-black/60 px-1.5 py-0.5 text-left text-[11px] font-semibold text-white hover:underline"
          title={camera.name}
          onClick={() => onOpenFull?.(camera.camera_id)}
        >
          {camera.name}
        </button>
      ) : (
      <div className="space-y-1 p-2">
        <div className="flex items-start justify-between gap-2">
          <button
            className="truncate text-left text-[13px] font-semibold text-ink-900 hover:underline"
            title={camera.name}
            onClick={() => onOpenFull?.(camera.camera_id)}
          >
            {camera.name}
          </button>
          <span className="shrink-0 text-2xs uppercase text-ink-500">{camera.camera_type}</span>
        </div>

        <div className="mono text-2xs text-ink-500">{camera.camera_id}</div>

        <dl className="grid grid-cols-2 gap-x-2 gap-y-0.5 text-2xs text-ink-500">
          <div className="col-span-2 truncate" title={loc.road_or_junction ?? ""}>
            {loc.district}
            {loc.road_or_junction ? ` · ${loc.road_or_junction}` : ""}
          </div>
          <div>
            {loc.latitude != null && loc.longitude != null ? (
              <a
                className="hover:underline"
                href={`https://www.google.com/maps?q=${loc.latitude},${loc.longitude}`}
                target="_blank"
                rel="noreferrer"
              >
                {loc.latitude.toFixed(4)}, {loc.longitude.toFixed(4)}
              </a>
            ) : (
              "no coordinate"
            )}
          </div>
          <div className="text-right">{loc.view_direction}</div>
          <div>
            {camera.technical_summary.resolution ?? "res —"}
            {camera.technical_summary.codec ? ` · ${camera.technical_summary.codec}` : ""}
          </div>
          <div className="truncate text-right" title={camera.owning_department}>
            {camera.owning_department}
          </div>
        </dl>
      </div>
      )}
    </div>
  );
}

/**
 * What the black rectangle says about itself.
 *
 * "Queued" and "Opening" are different facts and an operator watching a wall
 * fill in should be able to tell them apart - one means the wall is working
 * through its list, the other means this camera is being contacted right now.
 */
function statusText(
  phase: Phase,
  visible: boolean,
  error: string | null,
  attempt: number,
): string {
  if (!visible) return "Scroll into view to start";
  if (phase === "waiting") {
    return `${error ?? "Feed unavailable"} — retrying (${attempt + 1})`;
  }
  if (phase === "queued") return "Queued…";
  return error ?? "Opening…";
}

function describe(err: unknown): string {
  if (err instanceof ApiError) {
    const detail = err.detail as { message?: string } | string | undefined;
    if (typeof detail === "string") return detail;
    if (detail?.message) return detail.message;
  }
  return err instanceof Error ? err.message : String(err);
}


/**
 * A live-wall tile backed by server-decoded still frames.
 *
 * It holds no session and plays no video: it points an <img> at the camera's
 * snapshot endpoint and reloads it on a timer while the tile is on screen. The
 * edge worker refreshes each camera's frame as its decoder pool comes round,
 * so a tile shows the most recent frame at all times and never a black
 * rectangle. Only visible tiles poll, so a scrolled wall does not hammer the
 * API for cameras nobody is looking at.
 */
function SnapshotTile({
  camera,
  compact,
  onOpenFull,
}: {
  camera: Camera;
  compact: boolean;
  onOpenFull?: (cameraId: string) => void;
}) {
  const holderRef = useRef<HTMLDivElement | null>(null);
  const [visible, setVisible] = useState(false);
  const [src, setSrc] = useState<string | null>(null);
  const [everLoaded, setEverLoaded] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const node = holderRef.current;
    if (!node) return;
    const observer = new IntersectionObserver(
      ([entry]) => setVisible(entry.isIntersecting),
      { rootMargin: "150px" },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!visible) return;
    let alive = true;
    const refresh = () => {
      if (alive) setSrc(api.snapshotUrl(camera.camera_id, Date.now()));
    };
    refresh();
    const timer = setInterval(refresh, 2500);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [visible, camera.camera_id]);

  return (
    <div
      ref={holderRef}
      className={
        compact
          ? "relative h-full min-h-0 w-full overflow-hidden bg-black"
          : "overflow-hidden rounded border border-line bg-black"
      }
    >
      <div className={compact ? "relative h-full w-full bg-black" : "relative aspect-video bg-black"}>
        {src && (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={src}
            alt={camera.name}
            className={compact ? "h-full w-full object-cover" : "h-full w-full object-cover"}
            onLoad={() => {
              setEverLoaded(true);
              setFailed(false);
            }}
            onError={() => setFailed(true)}
          />
        )}
        {!everLoaded && (
          <div className="absolute inset-0 flex items-center justify-center text-2xs text-ink-500">
            {visible ? (failed ? "waiting for first frame…" : "connecting…") : "scroll into view"}
          </div>
        )}
        <span className="pointer-events-none absolute left-1.5 top-1.5 inline-flex items-center gap-1 rounded bg-black/60 px-1.5 py-0.5 text-2xs font-semibold uppercase tracking-wide text-white">
          <span className={`h-1.5 w-1.5 rounded-full ${everLoaded && !failed ? "animate-pulse bg-bad" : "bg-ink-500"}`} />
          Live
        </span>
        <button
          className="absolute bottom-1 left-1 max-w-[80%] truncate rounded bg-black/60 px-1.5 py-0.5 text-left text-[11px] font-semibold text-white hover:underline"
          title={camera.name}
          onClick={() => onOpenFull?.(camera.camera_id)}
        >
          {camera.name}
        </button>
      </div>
    </div>
  );
}
