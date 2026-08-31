"use client";

/** Shared presentation primitives for the registry console. */
import Link from "next/link";
import { useId, type ReactNode } from "react";
import {
  Archive,
  Ban,
  CheckCheck,
  ChevronRight,
  CircleAlert,
  CircleCheck,
  CircleHelp,
  CircleSlash,
  CircleX,
  Clock,
  History,
  Inbox,
  Info,
  Lock,
  LockKeyhole,
  PauseCircle,
  PencilLine,
  Radio,
  Send,
  ShieldX,
  TimerOff,
  TriangleAlert,
  Video,
  VideoOff,
  type LucideIcon,
} from "lucide-react";

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

/**
 * State is carried by icon *and* colour *and* text, never colour alone - these
 * pills are read in dense tables, and a red/amber difference is exactly what a
 * colour-blind operator or a washed-out control-room monitor loses first.
 */
export function Pill({
  tone,
  icon: Icon,
  children,
}: {
  tone: Tone;
  icon?: LucideIcon;
  children: ReactNode;
}) {
  return (
    <span className={`pill ${TONE_CLASS[tone]}`}>
      {Icon && <Icon className="h-3 w-3 shrink-0" strokeWidth={2} aria-hidden />}
      {children}
    </span>
  );
}

const HEALTH_META: Record<CameraHealthStatus, [Tone, LucideIcon]> = {
  online: ["ok", CircleCheck],
  degraded: ["warn", TriangleAlert],
  offline: ["bad", CircleX],
  unavailable: ["idle", CircleSlash],
  unknown: ["idle", CircleHelp],
};

export function HealthPill({ status }: { status: CameraHealthStatus | string }) {
  const [tone, icon] = HEALTH_META[status as CameraHealthStatus] ?? ["idle", CircleHelp];
  return (
    <Pill tone={tone} icon={icon}>
      {status}
    </Pill>
  );
}

const REQUEST_META: Record<RequestStatus, [Tone, LucideIcon]> = {
  DRAFT: ["idle", PencilLine],
  SUBMITTED: ["info", Send],
  VALIDATION_FAILED: ["bad", CircleX],
  REGISTERED: ["ok", CircleCheck],
  SYNCHRONIZED: ["ok", CheckCheck],
  SUSPENDED: ["warn", PauseCircle],
  DECOMMISSIONED: ["idle", Archive],
};

export function RequestStatusPill({ status }: { status: RequestStatus | string }) {
  const [tone, icon] = REQUEST_META[status as RequestStatus] ?? ["idle", CircleHelp];
  return (
    <Pill tone={tone} icon={icon}>
      {String(status).replace(/_/g, " ")}
    </Pill>
  );
}

const GRANT_META: Record<GrantStatus, [Tone, LucideIcon]> = {
  requested: ["warn", Clock],
  granted: ["ok", CircleCheck],
  denied: ["bad", CircleX],
  revoked: ["idle", Ban],
  expired: ["idle", TimerOff],
};

export function GrantStatusPill({ status }: { status: GrantStatus | string }) {
  const [tone, icon] = GRANT_META[status as GrantStatus] ?? ["idle", CircleHelp];
  return (
    <Pill tone={tone} icon={icon}>
      {status}
    </Pill>
  );
}

/** What this account may do with a camera's footage, in plain words. */
const VIDEO_STATE_LABEL: Record<VideoAccessState, [string, Tone, LucideIcon]> = {
  live_and_playback: ["Live + playback", "ok", Video],
  live_only: ["Live only", "ok", Radio],
  playback_only: ["Playback only", "ok", History],
  // Not a dead end - it is an instruction, so the registry turns this one into
  // a button rather than a greyed-out label.
  needs_unit_approval: ["Request from owner", "warn", LockKeyhole],
  not_enabled_by_owner: ["Owner has video off", "idle", VideoOff],
  camera_unavailable: ["Camera unavailable", "idle", CircleSlash],
  denied: ["No video", "idle", Ban],
};

export function VideoStatePill({ state }: { state: VideoAccessState | string }) {
  const [label, tone, icon] = VIDEO_STATE_LABEL[state as VideoAccessState] ?? [
    String(state).replace(/_/g, " "),
    "idle" as Tone,
    CircleHelp,
  ];
  return (
    <Pill tone={tone} icon={icon}>
      {label}
    </Pill>
  );
}

const INSTALLATION_META: Record<InstallationStatus, [Tone, LucideIcon]> = {
  COMMISSIONED: ["ok", CircleCheck],
  SUSPENDED: ["warn", PauseCircle],
  DECOMMISSIONED: ["idle", Archive],
};

export function InstallationPill({ status }: { status: InstallationStatus | string }) {
  const [tone, icon] = INSTALLATION_META[status as InstallationStatus] ?? ["idle", CircleHelp];
  return (
    <Pill tone={tone} icon={icon}>
      {String(status).replace(/_/g, " ")}
    </Pill>
  );
}

const SYNC_META: Record<SyncStatus, [Tone, LucideIcon]> = {
  SYNCHRONIZED: ["ok", CheckCheck],
  PENDING: ["warn", Clock],
  WITHDRAWN: ["idle", Ban],
  FAILED: ["bad", CircleX],
};

export function SyncPill({ status }: { status: SyncStatus | string }) {
  const [tone, icon] = SYNC_META[status as SyncStatus] ?? ["idle", CircleHelp];
  return (
    <Pill tone={tone} icon={icon}>
      {String(status)}
    </Pill>
  );
}

export function OutcomePill({ outcome }: { outcome: string }) {
  const [tone, icon]: [Tone, LucideIcon] =
    outcome === "denied"
      ? ["bad", ShieldX]
      : outcome === "error"
        ? ["bad", CircleX]
        : outcome === "partial"
          ? ["warn", TriangleAlert]
          : ["ok", CircleCheck];
  return (
    <Pill tone={tone} icon={icon}>
      {outcome}
    </Pill>
  );
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
                {index > 0 && <ChevronRight className="h-3 w-3 text-ink-400" strokeWidth={2} aria-hidden />}
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

/**
 * An empty table should read as "nothing matched", not as "this broke". The
 * icon is what separates those two readings before the sentence is read.
 */
export function EmptyState({
  message,
  hint,
  icon: Icon = Inbox,
}: {
  message: string;
  hint?: string;
  icon?: LucideIcon;
}) {
  return (
    <div className="px-4 py-10 text-center">
      <Icon
        className="mx-auto mb-2 h-7 w-7 text-ink-300"
        strokeWidth={1.4}
        aria-hidden
      />
      <p className="text-[13px] text-ink-500">{message}</p>
      {hint && <p className="mt-1 text-2xs text-ink-400">{hint}</p>}
    </div>
  );
}

const NOTICE_META: Record<Tone, [string, LucideIcon, string]> = {
  ok: ["border-l-ok bg-ok-bg", CircleCheck, "text-ok"],
  warn: ["border-l-warn bg-warn-bg", TriangleAlert, "text-warn"],
  bad: ["border-l-bad bg-bad-bg", CircleAlert, "text-bad"],
  idle: ["border-l-idle bg-idle-bg", Info, "text-idle"],
  info: ["border-l-brand-600 bg-brand-50", Info, "text-brand-600"],
};

export function Notice({
  tone = "info",
  title,
  children,
}: {
  tone?: Tone;
  title?: string;
  children: ReactNode;
}) {
  const [border, Icon, iconColour] = NOTICE_META[tone];
  return (
    <div
      className={`flex items-start gap-2 rounded border border-line border-l-4 px-3 py-2 text-[13px] ${border}`}
    >
      <Icon
        className={`mt-0.5 h-3.5 w-3.5 shrink-0 ${iconColour}`}
        strokeWidth={2}
        aria-hidden
      />
      <div className="min-w-0 flex-1">
        {title && <div className="font-semibold">{title}</div>}
        <div className={title ? "mt-0.5 text-ink-700" : "text-ink-700"}>{children}</div>
      </div>
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

/* -------------------------------------------------------------------------- */
/* Floating-label form controls.                                              */

/** useId, not a module counter - the latter desynchronises across the
 *  server and client renders and trips a hydration mismatch. */
function useFieldId(explicit?: string) {
  const generated = useId();
  return explicit ?? generated;
}

type FloatWrapProps = {
  label: string;
  id?: string;
  required?: boolean;
  error?: string | null;
  hint?: string;
  /** Classes for the wrapper (layout: width, grid span). */
  className?: string;
  /** Classes for the control itself (e.g. `mono`). */
  inputClassName?: string;
};

/**
 * `placeholder=" "` is not cosmetic - the raised/at-rest state is decided by
 * `:placeholder-shown`, so a control without it never drops its label back
 * down, and one with a *real* placeholder never shows it at rest.
 */
export function FloatInput({
  label,
  id,
  required,
  error,
  hint,
  className = "",
  inputClassName = "",
  ...rest
}: FloatWrapProps & React.InputHTMLAttributes<HTMLInputElement>) {
  const fieldId = useFieldId(id);
  return (
    <div className={className}>
      <div className="float">
        <input
          {...rest}
          id={fieldId}
          placeholder=" "
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? `${fieldId}-error` : undefined}
          className={`input ${inputClassName} ${error ? "input-error" : ""}`}
        />
        <label className="float-label" htmlFor={fieldId}>
          {label}
          {required && <span className="ml-0.5 text-bad">*</span>}
        </label>
      </div>
      <FieldFoot id={fieldId} error={error} hint={hint} />
    </div>
  );
}

export function FloatSelect({
  label,
  id,
  required,
  error,
  hint,
  className = "",
  inputClassName = "",
  children,
  ...rest
}: FloatWrapProps & React.SelectHTMLAttributes<HTMLSelectElement>) {
  const fieldId = useFieldId(id);
  return (
    <div className={className}>
      <div className="float">
        <select
          {...rest}
          id={fieldId}
          aria-invalid={error ? true : undefined}
          className={`select ${inputClassName} ${error ? "input-error" : ""}`}
        >
          {children}
        </select>
        <label className="float-label" htmlFor={fieldId}>
          {label}
          {required && <span className="ml-0.5 text-bad">*</span>}
        </label>
      </div>
      <FieldFoot id={fieldId} error={error} hint={hint} />
    </div>
  );
}

export function FloatTextarea({
  label,
  id,
  required,
  error,
  hint,
  className = "",
  inputClassName = "",
  ...rest
}: FloatWrapProps & React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  const fieldId = useFieldId(id);
  return (
    <div className={className}>
      <div className="float">
        <textarea
          {...rest}
          id={fieldId}
          placeholder=" "
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? `${fieldId}-error` : undefined}
          className={`textarea ${inputClassName} ${error ? "input-error" : ""}`}
        />
        <label className="float-label" htmlFor={fieldId}>
          {label}
          {required && <span className="ml-0.5 text-bad">*</span>}
        </label>
      </div>
      <FieldFoot id={fieldId} error={error} hint={hint} />
    </div>
  );
}

function FieldFoot({ id, error, hint }: { id: string; error?: string | null; hint?: string }) {
  if (error) {
    return (
      <p id={`${id}-error`} className="mt-1 text-2xs font-medium text-bad">
        {error}
      </p>
    );
  }
  if (hint) return <p className="mt-1 text-2xs text-ink-400">{hint}</p>;
  return null;
}

export function LockIcon({ className = "" }: { className?: string }) {
  return <Lock className={`h-3.5 w-3.5 text-navy-700 ${className}`} strokeWidth={1.6} aria-hidden />;
}
