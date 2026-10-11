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

  // No card: the form stands directly on the canvas, under a heading at full
  // weight and a line set light beside it. That pairing - 652 against 300 - is
  // the whole of the page's decoration.
  return (
    <div className="flex min-h-screen flex-col bg-canvas">
      <header className="px-6 pt-6 sm:px-8">
        <BrandLockup />
      </header>

      <main className="flex flex-1 items-center justify-center px-6 py-section">
        <div className="w-full max-w-[26rem]">
          <h1 className="text-h2 text-ink sm:text-h1">Sign in.</h1>
          <p className="mt-4 text-body-lg text-muted">
            Unified AI video intelligence for safer cities.
          </p>

          <form className="mt-10 space-y-3" onSubmit={submit}>
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

            <button className="btn btn-primary btn-lg w-full" type="submit" disabled={busy}>
              {busy ? <Spinner /> : <LogIn className="h-4 w-4" strokeWidth={2} aria-hidden />}
              {busy ? "Signing in…" : "Sign in"}
            </button>
          </form>
        </div>
      </main>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={<div className="p-8 text-body-sm text-muted">Loading…</div>}>
      <SignInForm />
    </Suspense>
  );
}
