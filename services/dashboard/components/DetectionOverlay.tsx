"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { api } from "@/lib/api";
import { CLASS_COLOUR } from "@/lib/constants";
import { footageTime } from "@/lib/format";
import type { Detection } from "@/lib/types";

/**
 * What the edge worker found, drawn over the feed it found it in.
 *
 * The detections table can tell you a car was seen at 1:54.698 with a box at
 * (0, 530)-(133, 608), and none of that is checkable by reading it. Drawing
 * the box on the picture is what makes it checkable: either there is a car
 * there or the model is wrong, and you can see which in one glance.
 *
 * Two things this deliberately does NOT claim:
 *
 * **These boxes are not synchronised to the frame on screen.** A live HLS
 * element's `currentTime` is a position in the browser's buffer, not the
 * source's presentation timestamp, and the two are minutes apart on a stream
 * that has been running a while. Pretending otherwise would draw boxes that
 * look authoritative and sit in the wrong places. Instead this shows a chosen
 * moment's detections, labelled with the media time they belong to, so the
 * operator can scrub there and compare deliberately.
 *
 * **Coordinates are source pixels.** The worker reports boxes in the frame it
 * decoded (1920x1080 for most grid cameras); the <video> is rendered at
 * whatever width the layout gives it. Scaling therefore uses the video's own
 * `videoWidth`/`videoHeight` rather than the CSS box, which is the only pair
 * that describes the coordinate space the boxes were measured in.
 */

/** Colour per class, so a glance separates people from vehicles. */
function colourFor(className: string): string {
  return CLASS_COLOUR[className] ?? "#94a3b8";
}

/** One decoded frame's worth of detections, keyed by the media time. */
interface Moment {
  pts: number | null;
  frameIndex: number | null;
  rows: Detection[];
}

/**
 * Group detections into the frames they came from.
 *
 * The worker submits every object it found in one frame as separate rows that
 * share a `frame_index`, so grouping by it reconstructs the frame - which is
 * the only unit worth drawing, since a box from one instant over a picture
 * from another is a lie.
 */
function toMoments(rows: Detection[]): Moment[] {
  const byFrame = new Map<string, Moment>();
  for (const row of rows) {
    const frameIndex =
      typeof row.provenance?.frame_index === "number"
        ? (row.provenance.frame_index as number)
        : null;
    const pts =
      typeof row.provenance?.pts_seconds === "number"
        ? (row.provenance.pts_seconds as number)
        : null;
    const key = `${frameIndex ?? "?"}:${row.timestamp_utc}`;
    const existing = byFrame.get(key);
    if (existing) {
      existing.rows.push(row);
    } else {
      byFrame.set(key, { pts, frameIndex, rows: [row] });
    }
  }
  return [...byFrame.values()].sort((a, b) => (b.pts ?? 0) - (a.pts ?? 0));
}

/**
 * The state both halves share.
 *
 * Split in two because the boxes must live inside the video's own positioned
 * box to scale with the picture, while the controls must not - rendering them
 * together put a row of buttons on top of the footage.
 */
export function useDetectionMoments(cameraId: string, video: HTMLVideoElement | null) {
  const [moments, setMoments] = useState<Moment[] | null>(null);
  const [index, setIndex] = useState(0);
  const [shown, setShown] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [natural, setNatural] = useState<{ w: number; h: number } | null>(null);

  // The intrinsic size arrives with the first decoded frame, not on mount.
  useEffect(() => {
    if (!video) return;
    const read = () => {
      if (video.videoWidth > 0) setNatural({ w: video.videoWidth, h: video.videoHeight });
    };
    read();
    video.addEventListener("loadedmetadata", read);
    return () => video.removeEventListener("loadedmetadata", read);
  }, [video]);

  const load = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const rows = await api.detections({
        camera_id: cameraId,
        since_hours: "24",
        limit: "400",
      });
      const grouped = toMoments(rows);
      setMoments(grouped);
      setIndex(0);
      setShown(true);
      if (grouped.length === 0) {
        setError("No detections recorded for this camera in the last 24 hours.");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, [cameraId]);

  const moment = moments && moments.length > 0 ? moments[index] : null;

  const boxes = useMemo(() => {
    if (!moment || !natural) return [];
    return moment.rows
      .filter((row) => row.bbox_xyxy.length === 4)
      .map((row) => {
        const [x1, y1, x2, y2] = row.bbox_xyxy;
        return {
          id: row.detection_id,
          label: `${row.class_name} ${row.confidence.toFixed(2)}`,
          plate: row.plate_text,
          colour: colourFor(String(row.class_name)),
          // Percentages, so the overlay tracks the video through any resize.
          left: (x1 / natural.w) * 100,
          top: (y1 / natural.h) * 100,
          width: ((x2 - x1) / natural.w) * 100,
          height: ((y2 - y1) / natural.h) * 100,
        };
      });
  }, [moment, natural]);

  return {
    moments,
    moment,
    boxes,
    index,
    setIndex,
    shown,
    setShown,
    busy,
    error,
    load,
  };
}

type Moments = ReturnType<typeof useDetectionMoments>;

/** The boxes themselves. Render INSIDE the video's positioned container. */
export function DetectionBoxes({ state }: { state: Moments }) {
  if (!state.shown || state.boxes.length === 0) return null;
  return (
    <div className="pointer-events-none absolute inset-0">
      {state.boxes.map((box) => (
        <div
          key={box.id}
          className="absolute"
          style={{
            left: `${box.left}%`,
            top: `${box.top}%`,
            width: `${box.width}%`,
            height: `${box.height}%`,
            border: `2px solid ${box.colour}`,
            boxShadow: "0 0 0 1px rgba(0,0,0,.55)",
          }}
        >
          <span
            className="absolute left-0 top-0 -translate-y-full whitespace-nowrap px-1 text-2xs font-medium"
            style={{ background: box.colour, color: "#0b1220" }}
          >
            {box.label}
            {box.plate ? ` · ${box.plate}` : ""}
          </span>
        </div>
      ))}
    </div>
  );
}

/** Load, step through frames, and say what is being shown. Render BELOW. */
export function DetectionControls({ state }: { state: Moments }) {
  const { moments, moment, boxes, index, setIndex, shown, setShown, busy, error, load } = state;
  return (
    <>
      <div className="mt-2 flex flex-wrap items-center gap-2 text-2xs">
        <button className="btn btn-sm" type="button" onClick={() => void load()} disabled={busy}>
          {busy ? "Loading…" : shown ? "Reload detections" : "Show what was detected"}
        </button>

        {shown && moments && moments.length > 0 && (
          <>
            <button
              className="btn btn-sm"
              type="button"
              onClick={() => setIndex((n) => Math.max(0, n - 1))}
              disabled={index === 0}
            >
              ← newer
            </button>
            <button
              className="btn btn-sm"
              type="button"
              onClick={() => setIndex((n) => Math.min(moments.length - 1, n + 1))}
              disabled={index >= moments.length - 1}
            >
              older →
            </button>
            <span className="text-ink-500">
              frame {index + 1} of {moments.length} · {boxes.length} object
              {boxes.length === 1 ? "" : "s"}
              {moment?.pts != null && (
                <>
                  {" · at "}
                  <span className="mono">{footageTime(moment.pts)}</span>
                  {" in the footage"}
                </>
              )}
              {moment?.frameIndex != null && ` · frame #${moment.frameIndex}`}
            </span>
            <button className="btn btn-sm" type="button" onClick={() => setShown((v) => !v)}>
              {shown ? "Hide boxes" : "Show boxes"}
            </button>
          </>
        )}
      </div>

      {shown && (
        <p className="mt-1 text-2xs leading-relaxed text-ink-400">
          Boxes are what the edge worker recorded at the media time shown, drawn in the
          coordinates it measured them in. They are <strong>not</strong> aligned to the frame
          playing above — a live stream&apos;s position is a buffer offset, not the
          source&apos;s presentation timestamp. Scrub to the stated time to compare.
        </p>
      )}

      {error && <p className="mt-1 text-2xs text-ink-500">{error}</p>}
    </>
  );
}
