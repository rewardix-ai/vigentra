"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { LogIn } from "lucide-react";

import { signIn } from "@/lib/api";
import {
  FloatInput,
  Notice,
  Spinner,
} from "@/components/ui";
import { BrandLockup } from "@/components/Brand";

function SignInForm() {
  const router = useRouter();
  const params = useSearchParams();
  // Only ever a same-origin path. An absolute or protocol-relative `next`
  // turned the sign-in page into an open redirect to any site.
  const requested = params.get("next");
  const next =
    requested && requested.startsWith("/") && !requested.startsWith("//") && !requested.startsWith("/\\")
      ? requested
      : "/";

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await signIn(username.trim(), password);
      router.push(next);
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-paper px-5 py-10">
      <div className="card w-full max-w-sm px-7 py-8">
        <div className="mb-7 flex justify-center">
          <BrandLockup size="lg" />
        </div>

        <h1 className="text-lg font-semibold text-ink-900">Sign in</h1>

          <form className="mt-4 space-y-3" onSubmit={submit}>
            {/* The leading icons are gone rather than combined with the label:
                at rest the label sits exactly where the icon did, and running
                both pushes the label off the field's text baseline. */}
            <FloatInput
              id="username"
              label="Username"
              autoComplete="username"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              required
            />
            <FloatInput
              id="password"
              label="Password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />

            {error && <Notice tone="bad">{error}</Notice>}

            <button className="btn btn-primary w-full" type="submit" disabled={busy}>
              {busy ? <Spinner /> : <LogIn className="h-3.5 w-3.5" strokeWidth={2} aria-hidden />}
              {busy ? "Signing in…" : "Sign in"}
            </button>
          </form>
      </div>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={<div className="p-8 text-[13px] text-ink-500">Loading…</div>}>
      <SignInForm />
    </Suspense>
  );
}
