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

/**
 * Where to go after signing in: `next`, but only ever a path on this site.
 *
 * A prefix check is not enough. Browsers strip tab, CR and LF from anywhere in
 * a URL, so "/\t/evil.com" passed a starts-with-"/" test and resolved to
 * evil.com. Reject control characters and backslashes outright, then resolve
 * against this origin and require it to stay here. Called at submit time, in
 * the browser, where `window` exists.
 */
function sameOriginPath(requested: string | null): string {
  if (!requested || /[\u0000-\u001f\u007f\\]/.test(requested)) return "/";
  try {
    const url = new URL(requested, window.location.origin);
    return url.origin === window.location.origin ? url.pathname + url.search + url.hash : "/";
  } catch {
    return "/";
  }
}

function SignInForm() {
  const router = useRouter();
  const params = useSearchParams();
  const requested = params.get("next");

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
      router.push(sameOriginPath(requested));
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
