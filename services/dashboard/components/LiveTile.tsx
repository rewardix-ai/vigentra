"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { api, ApiError } from "@/lib/api";
import type { Camera, VideoSession } from "@/lib/types";

/**
 * One camera on the live wall.
 *
 * A tile only opens a session when it is actually on screen, and gives it back
 * the moment it scrolls away. That is not an optimisation detail — the grid's
 * own guidance is "each connected client receives its own copy of the stream;
 * open only the cameras you are actively processing". Thirty permanent
 * sessions for a wall the operator is scrolling past would be exactly the
 * abuse that warns against, and each one is an audited access besides.
 */
export function LiveTile({
  camera,
  reason,
  password,
  onOpenFull,
}: {
  camera: Camera;
  reason: string;
  /** Confirmed once for the wall; each tile still re-authenticates per session. */
  password: string;
  onOpenFull?: (cameraId: string) => void;
}) {
  const [session, setSession] = useState<VideoSession | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [visible, setVisible] = useState(false);
  const holderRef = useRef<HTMLDivElement | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const sessionIdRef = useRef<string | null>(null);

  const watchable = camera.video_access === "live_and_playback" || camera.video_access === "live_only";

  useEffect(() => {
    sessionIdRef.current = session?.session_id ?? null;
  }, [session]);

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
    let cancelled = false;

    if (!visible || !watchable) {
      void close();
      return;
    }
    if (sessionIdRef.current) return;

    void (async () => {
      try {
        const opened = await api.openVideoSession({
          camera_id: camera.camera_id,
          mode: "live",
          reason,
          password,
        });
        if (cancelled) {
          void api.closeVideoSession(opened.session_id).catch(() => undefined);
          return;
        }
        setSession(opened);
        setError(null);
      } catch (err) {
        if (!cancelled) setError(describe(err));
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [visible, watchable, camera.camera_id, reason, password, close]);

  // Give the session back on unmount rather than leaving it to time out.
  useEffect(() => () => void close(), [close]);

  // Attach the feed. HLS needs hls.js outside Safari.
  useEffect(() => {
    const video = videoRef.current;
    if (!session || !video) return;

    const src = api.streamUrl(session);
    if (session.stream_protocol !== "hls") {
      video.src = src;
      return;
    }
    if (video.canPlayType("application/vnd.apple.mpegurl")) {
      video.src = src;
      return;
    }

    let destroyed = false;
    let hls: { destroy: () => void } | null = null;
    void import("hls.js").then(({ default: Hls }) => {
      if (destroyed || !Hls.isSupported()) return;
      const instance = new Hls({ lowLatencyMode: true, backBufferLength: 10 });
      hls = instance;
      instance.on(Hls.Events.ERROR, (_e: unknown, data: { fatal?: boolean }) => {
        if (data?.fatal) setError("Feed stopped");
      });
      instance.loadSource(src);
      instance.attachMedia(video);
    });
    return () => {
      destroyed = true;
      hls?.destroy();
    };
  }, [session]);

  const loc = camera.location;

  return (
    <div ref={holderRef} className="overflow-hidden rounded border border-line bg-white">
      <div className="relative aspect-video bg-black">
        {session ? (
          <>
            <video
              ref={videoRef}
              key={session.session_id}
              className="h-full w-full object-cover"
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
              : error ?? (visible ? "Opening…" : "Scroll into view to start")}
          </div>
        )}
      </div>

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
    </div>
  );
}

function describe(err: unknown): string {
  if (err instanceof ApiError) {
    const detail = err.detail as { message?: string } | string | undefined;
    if (typeof detail === "string") return detail;
    if (detail?.message) return detail.message;
  }
  return err instanceof Error ? err.message : String(err);
}
