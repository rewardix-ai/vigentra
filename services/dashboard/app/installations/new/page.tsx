"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { LoadingPanel } from "@/components/Shell";
import {
  InstallationForm,
  toPayload,
} from "@/components/InstallationForm";
import { Notice, PageHeader } from "@/components/ui";
import { api } from "@/lib/api";
import type { InstallationFormValues, Operator } from "@/lib/types";

export default function NewInstallationPage() {
  const router = useRouter();
  const [operator, setOperator] = useState<Operator | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .me()
      .then((me) => {
        setOperator(me);
        if (!me.permissions.includes("installation:create")) {
          setError(
            `Your role (${me.role.replace(/_/g, " ")}) cannot raise installation forms. ` +
              "Ask an installation operator in your department to raise it.",
          );
        }
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  }, []);

  async function submit(values: InstallationFormValues, submitForApproval: boolean) {
    setBusy(true);
    setError(null);
    try {
      const draft = await api.createInstallationRequest(toPayload(values));
      if (!submitForApproval) {
        router.push(`/installations/${encodeURIComponent(draft.request_id)}`);
        return;
      }
      const submitted = await api.submitRequest(draft.request_id);
      if (submitted.validation_errors.length > 0) {
        setError(
          `Your department's system reported validation issues: ${submitted.validation_errors.join("; ")}. ` +
            "The record is saved as a draft — correct the fields and resubmit.",
        );
      }
      router.push(`/installations/${encodeURIComponent(draft.request_id)}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <LoadingPanel label="Loading form" />;

  return (
    <>
      <PageHeader
        breadcrumb={[{ label: "Installation requests", href: "/installations" }, { label: "New" }]}
        title="New CCTV installation"
        subtitle={
          operator
            ? `Raising for ${operator.department}${operator.unit ? ` · ${operator.unit}` : ""}`
            : undefined
        }
      />

      {!operator?.permissions.includes("installation:create") && error ? (
        <Notice tone="bad">{error}</Notice>
      ) : (
        <InstallationForm
          operator={operator}
          onSubmit={submit}
          busy={busy}
          serverError={error}
        />
      )}
    </>
  );
}
