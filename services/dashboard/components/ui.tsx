/** Shared presentation primitives for the registry console. */
import Link from "next/link";
import type { ReactNode } from "react";

import { humanise } from "@/lib/format";
import type {
  CameraHealthStatus,
  GrantStatus,
  InstallationStatus,
  RequestStatus,
  SyncStatus,
  VideoAccessState,
} from "@/lib/types";

type Tone = "ok" | "warn" | "bad" | "idle" | "info";

const TONE_CLASS: Record<Tone, string> = {
  ok: "border-ok/30 bg-ok-bg text-ok",
  warn: "border-warn/30 bg-warn-bg text-warn",
  bad: "border-bad/30 bg-bad-bg text-bad",
  idle: "border-line-strong bg-idle-bg text-idle",
  info: "border-brand-500/30 bg-brand-100 text-brand-700",
};

export function Pill({ tone, children }: { tone: Tone; children: ReactNode }) {
  return <span className={`pill ${TONE_CLASS[tone]}`}>{children}</span>;
}

const HEALTH_TONE: Record<CameraHealthStatus, Tone> = {
  online: "ok",
  degraded: "warn",
  offline: "bad",
  unavailable: "idle",
  unknown: "idle",
};

export function HealthPill({ status }: { status: CameraHealthStatus | string }) {
  const tone = HEALTH_TONE[status as CameraHealthStatus] ?? "idle";
  return (
    <Pill tone={tone}>
      <span
        aria-hidden
        className={`h-1.5 w-1.5 rounded-full ${
          tone === "ok" ? "bg-ok" : tone === "warn" ? "bg-warn" : tone === "bad" ? "bg-bad" : "bg-idle"
        }`}
      />
      {status}
    </Pill>
  );
}

const REQUEST_TONE: Record<RequestStatus, Tone> = {
  DRAFT: "idle",
  SUBMITTED: "info",
  VALIDATION_FAILED: "bad",
  REGISTERED: "ok",
  SYNCHRONIZED: "ok",
  SUSPENDED: "warn",
  DECOMMISSIONED: "idle",
};

export function RequestStatusPill({ status }: { status: RequestStatus | string }) {
  return (
    <Pill tone={REQUEST_TONE[status as RequestStatus] ?? "idle"}>
      {String(status).replace(/_/g, " ")}
    </Pill>
  );
}

const GRANT_TONE: Record<GrantStatus, Tone> = {
  requested: "warn",
  granted: "ok",
  denied: "bad",
  revoked: "idle",
  expired: "idle",
};

export function GrantStatusPill({ status }: { status: GrantStatus | string }) {
  return <Pill tone={GRANT_TONE[status as GrantStatus] ?? "idle"}>{status}</Pill>;
}

/** What this account may do with a camera's footage, in plain words. */
const VIDEO_STATE_LABEL: Record<VideoAccessState, [string, Tone]> = {
  live_and_playback: ["Live + playback", "ok"],
  live_only: ["Live only", "ok"],
  playback_only: ["Playback only", "ok"],
  // Not a dead end - it is an instruction, so the registry turns this one into
  // a button rather than a greyed-out label.
  needs_unit_approval: ["Request from owner", "warn"],
  not_enabled_by_owner: ["Owner has video off", "idle"],
  camera_unavailable: ["Camera unavailable", "idle"],
  denied: ["No video", "idle"],
};

export function VideoStatePill({ state }: { state: VideoAccessState | string }) {
  const [label, tone] = VIDEO_STATE_LABEL[state as VideoAccessState] ?? [
    String(state).replace(/_/g, " "),
    "idle" as Tone,
  ];
  return <Pill tone={tone}>{label}</Pill>;
}

const INSTALLATION_TONE: Record<InstallationStatus, Tone> = {
  COMMISSIONED: "ok",
  SUSPENDED: "warn",
  DECOMMISSIONED: "idle",
};

export function InstallationPill({ status }: { status: InstallationStatus | string }) {
  return (
    <Pill tone={INSTALLATION_TONE[status as InstallationStatus] ?? "idle"}>
      {String(status).replace(/_/g, " ")}
    </Pill>
  );
}

const SYNC_TONE: Record<SyncStatus, Tone> = {
  SYNCHRONIZED: "ok",
  PENDING: "warn",
  WITHDRAWN: "idle",
  FAILED: "bad",
};

export function SyncPill({ status }: { status: SyncStatus | string }) {
  return <Pill tone={SYNC_TONE[status as SyncStatus] ?? "idle"}>{String(status)}</Pill>;
}

export function OutcomePill({ outcome }: { outcome: string }) {
  const tone: Tone =
    outcome === "denied" ? "bad" : outcome === "error" ? "bad" : outcome === "partial" ? "warn" : "ok";
  return <Pill tone={tone}>{outcome}</Pill>;
}

export function DepartmentTag({ department }: { department: string }) {
  const isTraffic = department === "Traffic Police";
  return (
    <span
      className={`pill ${
        isTraffic
          ? "border-brand-500/30 bg-brand-100 text-brand-700"
          : "border-navy-500/25 bg-[#eceff5] text-navy-600"
      }`}
    >
      {department}
    </span>
  );
}

/* -------------------------------------------------------------------------- */

export function Card({
  title,
  action,
  children,
  className = "",
}: {
  title?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`card ${className}`}>
      {title && (
        <header className="card-header">
          <h2 className="card-title">{title}</h2>
          {action}
        </header>
      )}
      {children}
    </section>
  );
}

export function Field({
  label,
  children,
  mono = false,
  redacted = false,
}: {
  label: string;
  children: ReactNode;
  mono?: boolean;
  redacted?: boolean;
}) {
  return (
    <div className="min-w-0">
      <div className="field-label">{label}</div>
      {redacted ? (
        <div className="field-value italic text-ink-400" title="Withheld for your role">
          withheld for your role
        </div>
      ) : (
        <div className={`field-value break-words ${mono ? "mono" : ""}`}>{children}</div>
      )}
    </div>
  );
}

export function PageHeader({
  title,
  subtitle,
  actions,
  breadcrumb,
}: {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
  breadcrumb?: { label: string; href?: string }[];
}) {
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
      <div>
        {breadcrumb && (
          <nav className="mb-1 flex items-center gap-1.5 text-2xs text-ink-500" aria-label="Breadcrumb">
            {breadcrumb.map((crumb, index) => (
              <span key={`${crumb.label}-${index}`} className="flex items-center gap-1.5">
                {index > 0 && <span aria-hidden>›</span>}
                {crumb.href ? (
                  <Link href={crumb.href} className="hover:text-brand-600 hover:underline">
                    {crumb.label}
                  </Link>
                ) : (
                  <span>{crumb.label}</span>
                )}
              </span>
            ))}
          </nav>
        )}
        <h1 className="text-lg font-semibold text-ink-900">{title}</h1>
        {subtitle && <p className="mt-0.5 text-[13px] text-ink-500">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Spinner({ className = "" }: { className?: string }) {
  return (
    <span
      role="status"
      aria-label="Loading"
      className={`inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent opacity-60 ${className}`}
    />
  );
}

export function EmptyState({ message, hint }: { message: string; hint?: string }) {
  return (
    <div className="px-4 py-10 text-center">
      <p className="text-[13px] text-ink-500">{message}</p>
      {hint && <p className="mt-1 text-2xs text-ink-400">{hint}</p>}
    </div>
  );
}

export function Notice({
  tone = "info",
  title,
  children,
}: {
  tone?: Tone;
  title?: string;
  children: ReactNode;
}) {
  const border = {
    ok: "border-l-ok bg-ok-bg",
    warn: "border-l-warn bg-warn-bg",
    bad: "border-l-bad bg-bad-bg",
    idle: "border-l-idle bg-idle-bg",
    info: "border-l-brand-600 bg-brand-50",
  }[tone];
  return (
    <div className={`rounded border border-line border-l-4 px-3 py-2 text-[13px] ${border}`}>
      {title && <div className="font-semibold">{title}</div>}
      <div className={title ? "mt-0.5 text-ink-700" : "text-ink-700"}>{children}</div>
    </div>
  );
}

/**
 * The Module 1 boundary, stated wherever a camera is on screen.
 *
 * Deliberately not dismissible: an operator should never be in doubt about
 * where footage lives or why there is no player on the page.
 */
/**
 * What happens to this camera's footage, said plainly.
 *
 * Without a `state` this is the general statement: Sentinel holds metadata,
 * departments hold video. With one it becomes specific to the camera in front
 * of the reader, because "you may watch this" and "ask the Municipal
 * Corporation" are very different things to tell an operator, and a single
 * generic banner tells them neither.
 */
export function FootageNotice({
  custodian,
  compact = false,
  state,
  children,
}: {
  custodian?: string;
  compact?: boolean;
  state?: VideoAccessState | string;
  children?: ReactNode;
}) {
  const owner = custodian ?? "the owning department";

  let headline = "Footage stays with the department that owns the camera";
  let body: ReactNode = (
    <>
      Sentinel federates camera <strong>metadata</strong>. Video is never copied here: a
      permitted session is brokered from {owner}&rsquo;s own CCTV/VMS environment, watermarked,
      time-limited and recorded in the audit log.
    </>
  );

  switch (state) {
    case "live_and_playback":
    case "live_only":
    case "playback_only":
      headline = "You may view this camera";
      body = (
        <>
          Sessions are short, watermarked with your username and the time, and written to the
          audit log. {owner} can withdraw this at any point.
        </>
      );
      break;
    case "needs_unit_approval":
      headline = `Ask ${owner} for footage`;
      body = (
        <>
          You can see this camera&rsquo;s record because the registry is shared across
          departments. Watching it is {owner}&rsquo;s decision, so raise a request saying which
          case it is for. Grants name one officer, expire on their own, and can be revoked.
        </>
      );
      break;
    case "not_enabled_by_owner":
      headline = "This camera is metadata-only";
      body = (
        <>
          {owner} has not enabled brokered video for this camera. No role in Sentinel can
          override that, and there is nothing to request.
        </>
      );
      break;
    case "camera_unavailable":
      headline = "No footage while the camera is unavailable";
      body = (
        <>
          The camera is suspended, decommissioned or offline. Its record stays in the registry so
          the asset is not lost, but there is no feed to broker.
        </>
      );
      break;
    default:
      break;
  }

  if (compact) {
    return (
      <div className="flex items-center gap-2 rounded border border-line bg-[#eef1f5] px-3 py-1.5 text-2xs text-ink-700">
        <LockIcon />
        <span>
          <strong className="font-semibold">{headline}.</strong>
          {custodian ? ` Custodian: ${custodian}.` : ""}
        </span>
      </div>
    );
  }

  return (
    <div className="flex items-start gap-2.5 rounded border border-line border-l-4 border-l-navy-700 bg-white px-3.5 py-2.5">
      <LockIcon className="mt-0.5 shrink-0" />
      <div className="min-w-0 flex-1 text-[13px]">
        <div className="font-semibold text-ink-900">{headline}</div>
        <p className="mt-0.5 text-ink-500">{body}</p>
        {children && <div className="mt-2">{children}</div>}
      </div>
    </div>
  );
}

export function LockIcon({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" className={`h-3.5 w-3.5 text-navy-700 ${className}`} fill="none" aria-hidden>
      <rect x="3" y="7" width="10" height="7" rx="1.5" stroke="currentColor" strokeWidth="1.3" />
      <path d="M5.5 7V5a2.5 2.5 0 0 1 5 0v2" stroke="currentColor" strokeWidth="1.3" />
    </svg>
  );
}
