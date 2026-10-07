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

/**
 * A tone colours the pill's icon and label, never its fill. `idle` is ink at
 * reduced opacity rather than the muted grey, which falls just short of 4.5:1
 * on the pill's own tint; `info` is plain ink - the electric blue is not a
 * status colour and is not spent here.
 */
const TONE_CLASS: Record<Tone, string> = {
  ok: "text-ok",
  warn: "text-warn",
  bad: "text-bad",
  idle: "text-ink/65",
  info: "text-ink",
};

/** Tones that report a condition, and so lead with a mark even with no icon. */
const SIGNAL_TONES = new Set<Tone>(["ok", "warn", "bad"]);

/**
 * State is carried by icon *and* colour *and* text, never colour alone - these
 * pills are read in dense tables, and a red/amber difference is exactly what a
 * colour-blind operator or a washed-out control-room monitor loses first.
 *
 * Labels are set in sentence case: the first letter is raised here, so a
 * caller can pass "online" or "3 open alerts" as it has them. A value that
 * arrives in capitals (an enum from the API) should come through `humanise`.
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
      {Icon ? (
        <Icon className="h-3.5 w-3.5 shrink-0" strokeWidth={2} aria-hidden />
      ) : (
        SIGNAL_TONES.has(tone) && (
          <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-current" aria-hidden />
        )
      )}
      <span className="inline-block first-letter:uppercase">{children}</span>
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
      {humanise(status)}
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
      {humanise(String(status))}
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
      {humanise(status)}
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
      {humanise(String(status))}
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
      {humanise(String(status))}
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

/**
 * Which department, as a plain capsule. The name is the whole message, so the
 * two departments are no longer told apart by a colour that meant nothing
 * without a key - on the map, where colour does separate them, the legend says so.
 */
export function DepartmentTag({ department }: { department: string }) {
  return <span className="pill">{department}</span>;
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

export function Stat({
  label,
  value,
  hint,
  tone = "plain",
  href,
}: {
  label: string;
  value: number | string;
  hint?: string;
  tone?: "plain" | "ok" | "warn" | "bad";
  href?: string;
}) {
  const colour = {
    plain: "text-ink",
    ok: "text-ok",
    warn: "text-warn",
    bad: "text-bad",
  }[tone];

  const body = (
    <>
      <div className="field-label">{label}</div>
      <div className={`tabular mt-2 text-h3 ${colour}`}>{value}</div>
      {hint && <div className="mt-2 text-caption text-muted">{hint}</div>}
    </>
  );

  // A figure that leads somewhere says so by taking the tint on hover - the
  // system's only way to raise a surface.
  if (href) {
    return (
      <Link
        href={href}
        className="card block px-6 py-5 transition-colors duration-150 hover:bg-canvas-soft"
      >
        {body}
      </Link>
    );
  }
  return <div className="card px-6 py-5">{body}</div>;
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
        <div className="field-value italic text-muted" title="Withheld for your role">
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
  // The title is the page's one loud thing: a 652-weight heading on white, with
  // the subtitle set quietly under it. No rule, no band - whitespace separates
  // the header from what follows.
  return (
    <div className="mb-8 flex flex-wrap items-end justify-between gap-x-6 gap-y-4">
      <div className="min-w-0">
        {breadcrumb && (
          <nav
            className="mb-3 flex flex-wrap items-center gap-1.5 text-caption text-muted"
            aria-label="Breadcrumb"
          >
            {breadcrumb.map((crumb, index) => (
              <span key={`${crumb.label}-${index}`} className="flex items-center gap-1.5">
                {index > 0 && <ChevronRight className="h-3 w-3 text-faint" strokeWidth={2} aria-hidden />}
                {crumb.href ? (
                  <Link href={crumb.href} className="underline-offset-4 hover:text-ink hover:underline">
                    {crumb.label}
                  </Link>
                ) : (
                  <span>{crumb.label}</span>
                )}
              </span>
            ))}
          </nav>
        )}
        <h1 className="text-h4 text-ink sm:text-h3">{title}</h1>
        {subtitle && <p className="mt-2 max-w-[70ch] text-body text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Spinner() {
  return (
    <span
      role="status"
      aria-label="Loading"
      className="inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent opacity-60"
    />
  );
}

/**
 * An empty table should read as "nothing matched", not as "this broke". The
 * icon is what separates those two readings before the sentence is read.
 */
export function EmptyState({ message, hint }: { message: string; hint?: string }) {
  return (
    <div className="px-6 py-12 text-center">
      <Inbox
        className="mx-auto mb-3 h-7 w-7 text-faint"
        strokeWidth={1.4}
        aria-hidden
      />
      <p className="text-body text-ink">{message}</p>
      {hint && <p className="mx-auto mt-1 max-w-[60ch] text-body-sm text-muted">{hint}</p>}
    </div>
  );
}

const NOTICE_META: Record<Tone, [LucideIcon, string]> = {
  ok: [CircleCheck, "text-ok"],
  warn: [TriangleAlert, "text-warn"],
  bad: [CircleAlert, "text-bad"],
  idle: [Info, "text-ink/65"],
  info: [Info, "text-ink"],
};

/**
 * A callout. Every tone sits on the same tinted panel; the icon is what says
 * which kind it is. Tinting the panel itself per tone would put a coloured
 * band across the page for every warning on it.
 */
export function Notice({
  tone = "info",
  title,
  children,
}: {
  tone?: Tone;
  title?: string;
  children: ReactNode;
}) {
  const [Icon, iconColour] = NOTICE_META[tone];
  return (
    <div className="panel flex items-start gap-3 px-4 py-3 text-body-sm">
      <Icon className={`mt-0.5 h-4 w-4 shrink-0 ${iconColour}`} strokeWidth={2} aria-hidden />
      <div className="min-w-0 flex-1">
        {title && <div className="font-semibold text-ink">{title}</div>}
        <div className={title ? "mt-0.5 text-ink-soft" : "text-ink-soft"}>{children}</div>
      </div>
    </div>
  );
}

/**
 * What happens to this camera's footage, said plainly.
 *
 * Without a `state` this is the general statement: Vigentra holds metadata,
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

  let headline = `Video is managed by ${owner}`;
  let body: ReactNode = <>Viewing sessions are time-limited and recorded in the audit log.</>;

  switch (state) {
    case "live_and_playback":
    case "live_only":
    case "playback_only":
      headline = "You may view this camera";
      body = <>Sessions are watermarked with your username and recorded in the audit log.</>;
      break;
    case "needs_unit_approval":
      headline = `Request access from ${owner}`;
      body = <>Raise a request with the case it&rsquo;s for. Approved access is personal and time-limited.</>;
      break;
    case "not_enabled_by_owner":
      headline = "Video isn't available for this camera";
      body = <>{owner} hasn&rsquo;t enabled video for this camera.</>;
      break;
    case "camera_unavailable":
      headline = "Camera unavailable";
      body = <>The camera is suspended, decommissioned or offline.</>;
      break;
    default:
      break;
  }

  if (compact) {
    return (
      <div className="panel flex items-center gap-2 px-4 py-2 text-caption text-ink-soft">
        <LockIcon />
        <span>
          <strong className="font-semibold">{headline}.</strong>
          {custodian ? ` Custodian: ${custodian}.` : ""}
        </span>
      </div>
    );
  }

  return (
    <div className="panel flex items-start gap-3 px-4 py-3">
      <LockIcon className="mt-0.5 shrink-0" />
      <div className="min-w-0 flex-1 text-body-sm">
        <div className="font-semibold text-ink">{headline}</div>
        <p className="mt-0.5 text-ink-soft">{body}</p>
        {children && <div className="mt-3">{children}</div>}
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

/** Set in from the field's own edge, so it lines up with the text above it. */
function FieldFoot({ id, error, hint }: { id: string; error?: string | null; hint?: string }) {
  if (error) {
    return (
      <p id={`${id}-error`} className="mt-1.5 px-4 text-caption font-semibold text-bad">
        {error}
      </p>
    );
  }
  if (hint) return <p className="mt-1.5 px-4 text-caption text-muted">{hint}</p>;
  return null;
}

export function LockIcon({ className = "" }: { className?: string }) {
  return <Lock className={`h-4 w-4 text-ink ${className}`} strokeWidth={1.8} aria-hidden />;
}
