"use client";

import { useMemo, useState } from "react";

import { Card, FootageNotice, Notice, Pill, Spinner } from "./ui";
import { titleise } from "@/lib/format";
import type {
  AttachmentRef,
  AttachmentTypeValue,
  CameraTypeValue,
  InstallationFormValues,
  LocalRoleValue,
  Operator,
  PurposeValue,
  SourceTypeValue,
} from "@/lib/types";

const CAMERA_TYPES: CameraTypeValue[] = [
  "fixed", "PTZ", "dome", "bullet", "ANPR-capable", "thermal", "other",
];
const PURPOSES: PurposeValue[] = [
  "traffic monitoring", "public safety", "junction monitoring", "highway monitoring", "other",
];
const SOURCE_TYPES: SourceTypeValue[] = ["RTSP", "ONVIF", "VMS_API", "NVR", "other"];
const LOCAL_ROLES: { value: LocalRoleValue; help: string }[] = [
  { value: "department_operator", help: "Control-room operators in the owning department" },
  { value: "district_supervisor", help: "District-level supervisory officers" },
  { value: "state_supervisor", help: "State-level supervisory officers" },
  { value: "investigator", help: "Investigating officers working a case" },
  { value: "system_admin", help: "VMS administrators in the owning department" },
  { value: "auditor", help: "Departmental audit staff" },
];
const DOCUMENT_TYPES: AttachmentTypeValue[] = [
  "site_survey", "installation_certificate", "camera_photograph", "approval_document",
];

const STEPS = [
  "Camera identity",
  "Ownership and location",
  "Technical metadata",
  "Local access policy",
  "Review and submit",
];

export function emptyForm(operator: Operator | null): InstallationFormValues {
  return {
    camera_name: "",
    external_camera_id: "",
    camera_serial_number: "",
    camera_vendor: "",
    camera_model: "",
    camera_type: "fixed",
    installation_purpose: "public safety",

    owning_department: operator && operator.department !== "*" ? operator.department : "",
    owning_unit: operator?.unit ?? "",
    district: "",
    police_station_or_zone: "",
    local_admin_contact: "",
    maintenance_agency: "",
    installation_vendor: "",

    latitude: "",
    longitude: "",
    address_or_landmark: "",
    road_or_junction: "",
    view_direction: "",
    coverage_description: "",
    entry_exit_zone_description: "",

    source_type: "VMS_API",
    vms_name: "",
    vms_vendor: "",
    resolution: "",
    fps: "",
    codec: "",
    supports_live: true,
    supports_playback: true,
    retention_days: "",
    timezone: "Asia/Kolkata",
    installation_date: new Date().toISOString().slice(0, 10),
    commissioning_date: "",

    permitted_local_roles: ["department_operator"],
    attachments: [],
  };
}

type Errors = Partial<Record<keyof InstallationFormValues, string>>;

/** Client-side checks. The owning department re-validates on submit regardless. */
function validateStep(step: number, values: InstallationFormValues): Errors {
  const errors: Errors = {};
  const required = (field: keyof InstallationFormValues, label: string) => {
    if (!String(values[field] ?? "").trim()) errors[field] = `${label} is required`;
  };

  if (step === 0) {
    required("camera_name", "Camera name");
    required("external_camera_id", "Department camera ID");
    if (values.external_camera_id && !/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(values.external_camera_id)) {
      errors.external_camera_id = "Use letters, digits, dot, dash or underscore only";
    }
  }

  if (step === 1) {
    required("owning_department", "Owning department");
    required("owning_unit", "Owning unit");
    required("district", "District");
    required("road_or_junction", "Road or junction");
    required("view_direction", "View direction");

    const lat = Number(values.latitude);
    const lng = Number(values.longitude);
    if (!String(values.latitude).trim()) errors.latitude = "Latitude is required";
    else if (Number.isNaN(lat) || lat < -90 || lat > 90) errors.latitude = "Latitude must be between -90 and 90";
    if (!String(values.longitude).trim()) errors.longitude = "Longitude is required";
    else if (Number.isNaN(lng) || lng < -180 || lng > 180) errors.longitude = "Longitude must be between -180 and 180";
  }

  if (step === 2) {
    required("timezone", "Timezone");
    required("installation_date", "Installation date");
    if (values.fps && (Number.isNaN(Number(values.fps)) || Number(values.fps) < 1)) {
      errors.fps = "Frame rate must be a positive number";
    }
    if (values.retention_days && Number(values.retention_days) < 0) {
      errors.retention_days = "Retention cannot be negative";
    }
  }

  if (step === 3 && values.permitted_local_roles.length === 0) {
    errors.permitted_local_roles = "Select at least one local role";
  }

  return errors;
}

/** Convert the string-based form state into the API's typed payload. */
export function toPayload(values: InstallationFormValues): Record<string, unknown> {
  const numeric = (value: string) => (String(value).trim() === "" ? null : Number(value));
  const text = (value: string) => (String(value).trim() === "" ? null : value.trim());

  return {
    camera_name: values.camera_name.trim(),
    external_camera_id: values.external_camera_id.trim(),
    camera_serial_number: text(values.camera_serial_number),
    camera_vendor: text(values.camera_vendor),
    camera_model: text(values.camera_model),
    camera_type: values.camera_type,
    installation_purpose: values.installation_purpose,

    owning_department: values.owning_department.trim(),
    owning_unit: values.owning_unit.trim(),
    district: values.district.trim(),
    police_station_or_zone: text(values.police_station_or_zone),
    local_admin_contact: text(values.local_admin_contact),
    maintenance_agency: text(values.maintenance_agency),
    installation_vendor: text(values.installation_vendor),

    latitude: Number(values.latitude),
    longitude: Number(values.longitude),
    address_or_landmark: text(values.address_or_landmark),
    road_or_junction: values.road_or_junction.trim(),
    view_direction: values.view_direction.trim(),
    coverage_description: text(values.coverage_description),
    entry_exit_zone_description: text(values.entry_exit_zone_description),

    source_type: values.source_type,
    vms_name: text(values.vms_name),
    vms_vendor: text(values.vms_vendor),
    resolution: text(values.resolution),
    fps: numeric(values.fps),
    codec: text(values.codec),
    supports_live: values.supports_live,
    supports_playback: values.supports_playback,
    retention_days: numeric(values.retention_days),
    timezone: values.timezone.trim(),
    installation_date: values.installation_date,
    commissioning_date: text(values.commissioning_date),

    permitted_local_roles: values.permitted_local_roles,
    attachments: values.attachments,
  };
}

/* -------------------------------------------------------------------------- */

function Text({
  label,
  field,
  values,
  errors,
  onChange,
  type = "text",
  placeholder,
  hint,
  required = false,
  disabled = false,
}: {
  label: string;
  field: keyof InstallationFormValues;
  values: InstallationFormValues;
  errors: Errors;
  onChange: (patch: Partial<InstallationFormValues>) => void;
  type?: string;
  placeholder?: string;
  hint?: string;
  required?: boolean;
  disabled?: boolean;
}) {
  const id = `field-${String(field)}`;
  const error = errors[field];
  return (
    <div>
      <label className="field-label" htmlFor={id}>
        {label} {required && <span className="text-bad">*</span>}
      </label>
      <input
        id={id}
        type={type}
        disabled={disabled}
        className={`input mt-1 ${error ? "input-error" : ""}`}
        placeholder={placeholder}
        value={String(values[field] ?? "")}
        aria-invalid={Boolean(error)}
        aria-describedby={error ? `${id}-error` : undefined}
        onChange={(event) => onChange({ [field]: event.target.value } as Partial<InstallationFormValues>)}
      />
      {error ? (
        <p id={`${id}-error`} className="mt-1 text-2xs font-medium text-bad">
          {error}
        </p>
      ) : hint ? (
        <p className="mt-1 text-2xs text-ink-400">{hint}</p>
      ) : null}
    </div>
  );
}

function Choice({
  label,
  field,
  values,
  onChange,
  options,
  required = false,
}: {
  label: string;
  field: keyof InstallationFormValues;
  values: InstallationFormValues;
  onChange: (patch: Partial<InstallationFormValues>) => void;
  options: readonly string[];
  required?: boolean;
}) {
  const id = `field-${String(field)}`;
  return (
    <div>
      <label className="field-label" htmlFor={id}>
        {label} {required && <span className="text-bad">*</span>}
      </label>
      <select
        id={id}
        className="select mt-1"
        value={String(values[field] ?? "")}
        onChange={(event) => onChange({ [field]: event.target.value } as Partial<InstallationFormValues>)}
      >
        {options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    </div>
  );
}

function Area({
  label,
  field,
  values,
  onChange,
  hint,
}: {
  label: string;
  field: keyof InstallationFormValues;
  values: InstallationFormValues;
  onChange: (patch: Partial<InstallationFormValues>) => void;
  hint?: string;
}) {
  const id = `field-${String(field)}`;
  return (
    <div className="sm:col-span-2">
      <label className="field-label" htmlFor={id}>
        {label}
      </label>
      <textarea
        id={id}
        rows={2}
        className="textarea mt-1"
        value={String(values[field] ?? "")}
        onChange={(event) => onChange({ [field]: event.target.value } as Partial<InstallationFormValues>)}
      />
      {hint && <p className="mt-1 text-2xs text-ink-400">{hint}</p>}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="grid gap-x-5 gap-y-3.5 px-4 py-4 sm:grid-cols-2 lg:grid-cols-3">
      <div className="sm:col-span-2 lg:col-span-3">
        <h3 className="section-label">{title}</h3>
      </div>
      {children}
    </div>
  );
}

/* -------------------------------------------------------------------------- */

export function InstallationForm({
  operator,
  onSubmit,
  busy,
  serverError,
}: {
  operator: Operator | null;
  onSubmit: (values: InstallationFormValues, submitForApproval: boolean) => Promise<void>;
  busy: boolean;
  serverError: string | null;
}) {
  const [step, setStep] = useState(0);
  const [values, setValues] = useState<InstallationFormValues>(() => emptyForm(operator));
  const [errors, setErrors] = useState<Errors>({});
  const [touchedSteps, setTouchedSteps] = useState<number[]>([]);

  const change = (patch: Partial<InstallationFormValues>) => {
    setValues((current) => ({ ...current, ...patch }));
    setErrors((current) => {
      const next = { ...current };
      for (const key of Object.keys(patch)) delete next[key as keyof InstallationFormValues];
      return next;
    });
  };

  const departmentLocked = Boolean(operator && operator.department !== "*");

  // Keep the department in step with the signed-in account.
  useMemo(() => {
    if (departmentLocked && operator && values.owning_department !== operator.department) {
      setValues((current) => ({ ...current, owning_department: operator.department }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [operator?.department]);

  function goTo(target: number) {
    if (target > step) {
      const stepErrors = validateStep(step, values);
      setErrors(stepErrors);
      setTouchedSteps((current) => Array.from(new Set([...current, step])));
      if (Object.keys(stepErrors).length > 0) return;
    }
    setStep(Math.max(0, Math.min(STEPS.length - 1, target)));
  }

  async function finish(submitForApproval: boolean) {
    // Re-run every step's validation before anything leaves the browser.
    const all: Errors = {};
    for (let index = 0; index < STEPS.length - 1; index += 1) {
      Object.assign(all, validateStep(index, values));
    }
    if (Object.keys(all).length > 0) {
      setErrors(all);
      const firstBad = [0, 1, 2, 3].find(
        (index) => Object.keys(validateStep(index, values)).length > 0,
      );
      if (firstBad !== undefined) setStep(firstBad);
      return;
    }
    await onSubmit(values, submitForApproval);
  }

  function toggleRole(role: LocalRoleValue) {
    setValues((current) => ({
      ...current,
      permitted_local_roles: current.permitted_local_roles.includes(role)
        ? current.permitted_local_roles.filter((item) => item !== role)
        : [...current.permitted_local_roles, role],
    }));
    setErrors((current) => ({ ...current, permitted_local_roles: undefined }));
  }

  function addAttachment() {
    setValues((current) => ({
      ...current,
      attachments: [
        ...current.attachments,
        { document_type: "site_survey", reference: "", filename: "" },
      ],
    }));
  }

  function updateAttachment(index: number, patch: Partial<AttachmentRef>) {
    setValues((current) => ({
      ...current,
      attachments: current.attachments.map((item, position) =>
        position === index ? { ...item, ...patch } : item,
      ),
    }));
  }

  function removeAttachment(index: number) {
    setValues((current) => ({
      ...current,
      attachments: current.attachments.filter((_, position) => position !== index),
    }));
  }

  return (
    <div className="space-y-3">
      <Notice tone="info" title="Where this form goes">
        This form is submitted to <strong>{values.owning_department || "your department"}</strong>
        &rsquo;s own CCTV/VMS system and validated there. Once it passes, Sentinel receives that camera&rsquo;s{" "}
        <strong>metadata only</strong> — raw CCTV footage remains within the owning
        department&rsquo;s local environment.
      </Notice>

      {/* Stepper */}
      <nav aria-label="Form steps" className="card flex flex-wrap gap-1 px-2 py-2">
        {STEPS.map((label, index) => {
          const active = index === step;
          const done = touchedSteps.includes(index) && index < step;
          return (
            <button
              key={label}
              type="button"
              onClick={() => goTo(index)}
              aria-current={active ? "step" : undefined}
              className={`flex items-center gap-2 rounded px-2.5 py-1.5 text-[13px] transition ${
                active
                  ? "bg-brand-600 font-medium text-white"
                  : "text-ink-700 hover:bg-brand-50"
              }`}
            >
              <span
                className={`flex h-5 w-5 items-center justify-center rounded-full text-2xs font-semibold ${
                  active ? "bg-white text-brand-700" : done ? "bg-ok text-white" : "bg-idle-bg text-ink-500"
                }`}
              >
                {done ? "✓" : index + 1}
              </span>
              <span className="hidden sm:inline">{label}</span>
            </button>
          );
        })}
      </nav>

      {serverError && <Notice tone="bad" title="The department system rejected this form">{serverError}</Notice>}

      <Card title={`Step ${step + 1} of ${STEPS.length} — ${STEPS[step]}`}>
        {/* Step 1 --------------------------------------------------------- */}
        {step === 0 && (
          <Section title="Camera identity">
            <Text label="Camera name" field="camera_name" values={values} errors={errors} onChange={change} required placeholder="CG Road East" />
            <Text label="Department camera ID" field="external_camera_id" values={values} errors={errors} onChange={change} required placeholder="TRF-AHM-0007" hint="The ID this camera carries in your own VMS" />
            <Text label="Serial number" field="camera_serial_number" values={values} errors={errors} onChange={change} hint="Masked before it is shown centrally" />
            <Text label="Manufacturer" field="camera_vendor" values={values} errors={errors} onChange={change} />
            <Text label="Model" field="camera_model" values={values} errors={errors} onChange={change} />
            <Choice label="Camera type" field="camera_type" values={values} onChange={change} options={CAMERA_TYPES} required />
            <Choice label="Installation purpose" field="installation_purpose" values={values} onChange={change} options={PURPOSES} required />
          </Section>
        )}

        {/* Step 2 --------------------------------------------------------- */}
        {step === 1 && (
          <>
            <Section title="Ownership and administration">
              <div>
                <label className="field-label" htmlFor="field-owning_department">
                  Owning department <span className="text-bad">*</span>
                </label>
                <input
                  id="field-owning_department"
                  className={`input mt-1 ${errors.owning_department ? "input-error" : ""}`}
                  value={values.owning_department}
                  disabled={departmentLocked}
                  onChange={(event) => change({ owning_department: event.target.value })}
                />
                <p className="mt-1 text-2xs text-ink-400">
                  {departmentLocked
                    ? "Set from your account — you may only raise forms for your own department."
                    : "Statewide accounts must name the owning department."}
                </p>
              </div>
              <Text label="Owning unit" field="owning_unit" values={values} errors={errors} onChange={change} required placeholder="Ahmedabad Traffic Zone 1" />
              <Text label="Police station / zone" field="police_station_or_zone" values={values} errors={errors} onChange={change} />
              <Text label="Local administrator contact" field="local_admin_contact" values={values} errors={errors} onChange={change} hint="Held by your department; Sentinel receives it masked" />
              <Text label="Maintenance agency" field="maintenance_agency" values={values} errors={errors} onChange={change} />
              <Text label="Installation vendor" field="installation_vendor" values={values} errors={errors} onChange={change} />
            </Section>

            <div className="border-t border-line" />

            <Section title="Location and orientation">
              <Text label="District" field="district" values={values} errors={errors} onChange={change} required placeholder="Ahmedabad" />
              <Text label="Road / junction" field="road_or_junction" values={values} errors={errors} onChange={change} required placeholder="CG Road" />
              <Text label="Address or landmark" field="address_or_landmark" values={values} errors={errors} onChange={change} />
              <Text label="Latitude" field="latitude" values={values} errors={errors} onChange={change} required placeholder="23.0225" hint="-90 to 90" />
              <Text label="Longitude" field="longitude" values={values} errors={errors} onChange={change} required placeholder="72.5714" hint="-180 to 180" />
              <Text label="View direction" field="view_direction" values={values} errors={errors} onChange={change} required placeholder="eastbound" />
              <Area label="Coverage description" field="coverage_description" values={values} onChange={change} />
              <Area label="Entry / exit zone description" field="entry_exit_zone_description" values={values} onChange={change} hint="Optional. Recorded for future modules; unused in Module 1." />
            </Section>
          </>
        )}

        {/* Step 3 --------------------------------------------------------- */}
        {step === 2 && (
          <>
            <Section title="Technical metadata">
              <Choice label="Feed type" field="source_type" values={values} onChange={change} options={SOURCE_TYPES} required />
              <Text label="VMS name" field="vms_name" values={values} errors={errors} onChange={change} placeholder="Traffic VMS Demo" />
              <Text label="VMS vendor" field="vms_vendor" values={values} errors={errors} onChange={change} />
              <Text label="Resolution" field="resolution" values={values} errors={errors} onChange={change} placeholder="1920x1080" />
              <Text label="Frame rate (fps)" field="fps" values={values} errors={errors} onChange={change} type="number" />
              <Text label="Codec" field="codec" values={values} errors={errors} onChange={change} placeholder="H.264" />
              <Text label="Local retention (days)" field="retention_days" values={values} errors={errors} onChange={change} type="number" />
              <Text label="Timezone" field="timezone" values={values} errors={errors} onChange={change} required />
              <Text label="Installation date" field="installation_date" values={values} errors={errors} onChange={change} type="date" required />
              <Text label="Commissioning date" field="commissioning_date" values={values} errors={errors} onChange={change} type="date" hint="Set on approval if left blank" />
            </Section>

            <div className="border-t border-line" />

            <div className="px-4 py-4">
              <h3 className="section-label">Local recording capability</h3>
              <p className="mt-1 text-2xs text-ink-500">
                Recorded so the access policy can describe what your department&rsquo;s system
                offers. Sentinel exposes no viewing link either way.
              </p>
              <div className="mt-2.5 flex flex-wrap gap-4">
                <label className="flex items-center gap-2 text-[13px]">
                  <input
                    type="checkbox"
                    checked={values.supports_live}
                    onChange={(event) => change({ supports_live: event.target.checked })}
                  />
                  Supports live view in the local VMS
                </label>
                <label className="flex items-center gap-2 text-[13px]">
                  <input
                    type="checkbox"
                    checked={values.supports_playback}
                    onChange={(event) => change({ supports_playback: event.target.checked })}
                  />
                  Supports recorded playback in the local VMS
                </label>
              </div>
            </div>
          </>
        )}

        {/* Step 4 --------------------------------------------------------- */}
        {step === 3 && (
          <>
            <div className="px-4 pt-4">
              <FootageNotice custodian={values.owning_department || undefined} />
            </div>
            <div className="px-4 py-4">
              <h3 className="section-label">
                Roles permitted to view this camera&rsquo;s footage in your department&rsquo;s VMS{" "}
                <span className="text-bad">*</span>
              </h3>
              <p className="mt-1 text-2xs text-ink-500">
                These permissions apply to the owning department&rsquo;s local CCTV/VMS. Sentinel
                stores and displays the permission policy summary but does not provide video access.
              </p>
              {errors.permitted_local_roles && (
                <p className="mt-1.5 text-2xs font-medium text-bad">{errors.permitted_local_roles}</p>
              )}
              <div className="mt-3 grid gap-2 sm:grid-cols-2">
                {LOCAL_ROLES.map((role) => (
                  <label
                    key={role.value}
                    className={`flex cursor-pointer items-start gap-2.5 rounded border px-3 py-2 transition ${
                      values.permitted_local_roles.includes(role.value)
                        ? "border-brand-500 bg-brand-50"
                        : "border-line hover:bg-[#f7f9fb]"
                    }`}
                  >
                    <input
                      type="checkbox"
                      className="mt-0.5"
                      checked={values.permitted_local_roles.includes(role.value)}
                      onChange={() => toggleRole(role.value)}
                    />
                    <span>
                      <span className="block text-[13px] font-medium text-ink-900">
                        {role.value.replace(/_/g, " ")}
                      </span>
                      <span className="block text-2xs text-ink-500">{role.help}</span>
                    </span>
                  </label>
                ))}
              </div>
            </div>

            <div className="border-t border-line px-4 py-4">
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="section-label">Supporting document references</h3>
                  <p className="mt-1 text-2xs text-ink-500">
                    References only. Do not upload CCTV footage or credentials — documents stay with
                    your department.
                  </p>
                </div>
                <button type="button" className="btn btn-sm" onClick={addAttachment}>
                  Add reference
                </button>
              </div>

              {values.attachments.length > 0 && (
                <div className="mt-3 space-y-2">
                  {values.attachments.map((item, index) => (
                    <div key={index} className="grid gap-2 sm:grid-cols-[12rem_1fr_1fr_auto]">
                      <select
                        className="select"
                        value={item.document_type}
                        onChange={(event) =>
                          updateAttachment(index, {
                            document_type: event.target.value as AttachmentTypeValue,
                          })
                        }
                      >
                        {DOCUMENT_TYPES.map((type) => (
                          <option key={type} value={type}>
                            {titleise(type)}
                          </option>
                        ))}
                      </select>
                      <input
                        className="input"
                        placeholder="Department reference number"
                        value={item.reference}
                        onChange={(event) => updateAttachment(index, { reference: event.target.value })}
                      />
                      <input
                        className="input"
                        placeholder="Filename (optional)"
                        value={item.filename ?? ""}
                        onChange={(event) => updateAttachment(index, { filename: event.target.value })}
                      />
                      <button
                        type="button"
                        className="btn btn-danger btn-sm"
                        onClick={() => removeAttachment(index)}
                      >
                        Remove
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </>
        )}

        {/* Step 5 --------------------------------------------------------- */}
        {step === 4 && (
          <div className="px-4 py-4">
            <h3 className="section-label">Review</h3>
            <p className="mt-1 text-2xs text-ink-500">
              Saving creates a DRAFT in {values.owning_department}&rsquo;s register. Submitting sends
              it for that department&rsquo;s validation and approval.
            </p>

            <dl className="mt-3 grid gap-x-5 gap-y-2.5 sm:grid-cols-2 lg:grid-cols-3">
              {[
                ["Camera name", values.camera_name],
                ["Department camera ID", values.external_camera_id],
                ["Camera type", values.camera_type],
                ["Purpose", values.installation_purpose],
                ["Owning department", values.owning_department],
                ["Owning unit", values.owning_unit],
                ["District", values.district],
                ["Road / junction", values.road_or_junction],
                ["Coordinates", `${values.latitude}, ${values.longitude}`],
                ["View direction", values.view_direction],
                ["Feed type", values.source_type],
                ["VMS", values.vms_name || "—"],
                ["Resolution", values.resolution || "—"],
                ["Retention", values.retention_days ? `${values.retention_days} days` : "—"],
                ["Installed on", values.installation_date],
              ].map(([label, value]) => (
                <div key={label} className="min-w-0">
                  <dt className="field-label">{label}</dt>
                  <dd className="field-value break-words">{value || "—"}</dd>
                </div>
              ))}
            </dl>

            <div className="mt-4 border-t border-line pt-3">
              <div className="field-label">Local viewing roles</div>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {values.permitted_local_roles.map((role) => (
                  <Pill key={role} tone="info">
                    {role.replace(/_/g, " ")}
                  </Pill>
                ))}
                {values.permitted_local_roles.length === 0 && (
                  <span className="text-[13px] text-bad">None selected</span>
                )}
              </div>
            </div>

            <div className="mt-3 border-t border-line pt-3">
              <div className="field-label">Document references</div>
              <div className="mt-1 text-[13px]">
                {values.attachments.length === 0
                  ? "—"
                  : values.attachments
                      .map((item) => `${titleise(item.document_type)}: ${item.reference || "(no reference)"}`)
                      .join(" · ")}
              </div>
            </div>

            <div className="mt-4">
              <FootageNotice custodian={values.owning_department || undefined} />
            </div>
          </div>
        )}

        {/* Navigation ----------------------------------------------------- */}
        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line px-4 py-3">
          <button type="button" className="btn" onClick={() => goTo(step - 1)} disabled={step === 0 || busy}>
            Back
          </button>
          <div className="flex flex-wrap gap-2">
            {step < STEPS.length - 1 ? (
              <button type="button" className="btn btn-primary" onClick={() => goTo(step + 1)}>
                Continue
              </button>
            ) : (
              <>
                <button type="button" className="btn" onClick={() => finish(false)} disabled={busy}>
                  {busy && <Spinner />} Save as draft
                </button>
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={() => finish(true)}
                  disabled={busy}
                >
                  {busy && <Spinner />} Save and submit for approval
                </button>
              </>
            )}
          </div>
        </div>
      </Card>
    </div>
  );
}
