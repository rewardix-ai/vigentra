"use client";

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { LoadingPanel } from "@/components/Shell";
import { Card, Field, LockIcon, Notice, PageHeader, Pill } from "@/components/ui";
import { api } from "@/lib/api";
import { ist, orDash } from "@/lib/format";
import type { AccessPolicy, Operator } from "@/lib/types";

/** The three access concepts, stated separately so they cannot be conflated. */
function AccessRow({
  label,
  state,
  tone,
  description,
}: {
  label: string;
  state: string;
  tone: "ok" | "warn" | "bad" | "idle";
  description: string;
}) {
  return (
    <div className="flex items-start gap-4 border-b border-line px-4 py-3 last:border-b-0">
      <div className="w-52 shrink-0">
        <div className="text-[13px] font-medium text-ink-900">{label}</div>
      </div>
      <div className="w-40 shrink-0">
        <Pill tone={tone}>{state}</Pill>
      </div>
      <p className="text-[13px] text-ink-500">{description}</p>
    </div>
  );
}

export default function AccessPolicyPage() {
  const params = useParams<{ cameraId: string }>();
  const cameraId = decodeURIComponent(params.cameraId);

  const [policy, setPolicy] = useState<AccessPolicy | null>(null);
  const [operator, setOperator] = useState<Operator | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .accessPolicy(cameraId)
      .then(setPolicy)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
    api.me().then(setOperator).catch(() => undefined);
  }, [cameraId]);

  if (loading) return <LoadingPanel label="Loading access policy" />;

  if (error || !policy) {
    return (
      <>
        <PageHeader
          title="Access policy"
          breadcrumb={[{ label: "Camera registry", href: "/registry" }, { label: cameraId }]}
        />
        <Notice tone="bad">{error ?? "Policy unavailable"}</Notice>
      </>
    );
  }

  return (
    <>
      <PageHeader
        breadcrumb={[
          { label: "Camera registry", href: "/registry" },
          { label: policy.camera_id, href: `/registry/${encodeURIComponent(policy.camera_id)}` },
          { label: "Access policy" },
        ]}
        title={`Access policy — ${policy.camera_name}`}
        subtitle={`${policy.owning_department} · policy version ${policy.policy_version}`}
      />

      <div className="space-y-3">
        <div className="flex items-start gap-2.5 rounded border border-line border-l-4 border-l-navy-700 bg-white px-3.5 py-2.5">
          <LockIcon className="mt-0.5 shrink-0" />
          <div className="text-[13px]">
            <div className="font-semibold text-ink-900">
              Video access through Vigentra:{" "}
              {policy.vigentra_video_access ? "PERMITTED BY THE OWNER" : "NOT PERMITTED"}
            </div>
            <p className="mt-0.5 text-ink-500">{policy.vigentra_video_access_note}</p>
            <p className="mt-1 text-ink-500">
              Footage owner: <strong className="text-ink-900">{policy.footage_custodian}</strong>
            </p>
          </div>
        </div>

        <Card title="What this policy permits">
          <AccessRow
            label="Vigentra metadata access"
            state="Enabled by role"
            tone="ok"
            description={`Your account reads: ${policy.vigentra_metadata_access}. Registry, health and policy records only.`}
          />
          <AccessRow
            label="Local VMS video access"
            state={policy.local_video_access_enabled ? "Enabled" : "Disabled"}
            tone={policy.local_video_access_enabled ? "ok" : "idle"}
            description={`Controlled entirely by ${policy.footage_custodian} inside ${
              policy.local_vms_name ?? "its own CCTV/VMS system"
            }. Vigentra records the summary below but grants nothing and links to nothing.`}
          />
        </Card>

        <Card title="Roles permitted to view footage in the owning department's VMS">
          {policy.permitted_local_roles.length === 0 ? (
            <p className="px-4 py-4 text-[13px] text-ink-500">
              No local viewing roles recorded for this camera.
            </p>
          ) : (
            <ul className="divide-y divide-line">
              {policy.permitted_local_roles.map((role) => (
                <li key={role} className="flex items-center gap-3 px-4 py-2.5">
                  <Pill tone="idle">{role.replace(/_/g, " ")}</Pill>
                  <span className="text-[13px] text-ink-500">
                    may view this camera&rsquo;s footage inside {policy.footage_custodian}&rsquo;s
                    own system
                  </span>
                </li>
              ))}
            </ul>
          )}
          <p className="border-t border-line px-4 py-2 text-2xs leading-relaxed text-ink-500">
            This is a record of the owning department&rsquo;s policy, not a grant. These role names
            belong to that department&rsquo;s VMS and are unrelated to Vigentra roles. Vigentra
            provides no link, token or route to footage for any of them.
          </p>
        </Card>

        <Card title="Policy provenance">
          <div className="grid gap-x-5 gap-y-3.5 px-4 py-3.5 sm:grid-cols-2 lg:grid-cols-3">
            <Field label="Camera" mono>
              {policy.camera_id}
            </Field>
            <Field label="Owning department">{policy.owning_department}</Field>
            <Field label="Source system">{policy.source_system}</Field>
            <Field label="Local VMS">{orDash(policy.local_vms_name)}</Field>
            <Field label="Approved by role">{orDash(policy.approved_by_role)}</Field>
            <Field label="Approved at">{ist(policy.approved_at)}</Field>
            <Field label="Policy version">{policy.policy_version}</Field>
            <Field label="Your role">{orDash(operator?.role?.replace(/_/g, " "))}</Field>
            <Field label="Your metadata visibility">{orDash(operator?.visibility_level)}</Field>
          </div>
        </Card>
      </div>
    </>
  );
}
