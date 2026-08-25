"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { signIn } from "@/lib/api";
import { Notice, Spinner } from "@/components/ui";

interface DemoAccount {
  username: string;
  /** Filled by the Use button; never displayed. */
  password: string;
}

/**
 * Demo accounts, grouped by purpose.
 *
 * Only the username is shown - the Use button fills both fields, so there is
 * no reason to put a password on screen. The grouping stays because it IS the
 * role model: department operators can install, retire and manage their unit's
 * footage. Mirrors DEFAULT_DEMO_USERS in services/central-api/app/config.py.
 */
const GROUPS: { group: string; accounts: DemoAccount[] }[] = [
  {
    group: "Department operations · install, retire and manage footage",
    accounts: [
      { username: "traffic.installer", password: "Install@2026" },
      { username: "municipal.installer", password: "Install@2026" },
    ],
  },
  {
    group: "Operations · may view and manage video",
    accounts: [
      { username: "traffic.operator", password: "Traffic@2026" },
      { username: "traffic.zone3", password: "Traffic@2026" },
      { username: "municipal.operator", password: "Municipal@2026" },
      { username: "dept.admin", password: "DeptAdmin@2026" },
    ],
  },
  {
    group: "Oversight · metadata and full system access",
    accounts: [
      { username: "state.admin", password: "State@2026" },
      { username: "ahmedabad.cityadmin", password: "City@2026" },
      { username: "registry.viewer", password: "Registry@2026" },
      { username: "health.monitor", password: "Health@2026" },
      { username: "auditor", password: "Auditor@2026" },
      { username: "system.admin", password: "SysAdmin@2026" },
    ],
  },
  {
    group: "Analytics & reference",
    accounts: [
      { username: "ai.operator", password: "AiOps@2026" },
      { username: "vehicle.registry", password: "Vehicle@2026" },
    ],
  },
];

function SignInForm() {
  const router = useRouter();
  const params = useSearchParams();
  const next = params.get("next") || "/";

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

  function use(account: DemoAccount) {
    setUsername(account.username);
    setPassword(account.password);
    setError(null);
  }

  return (
    // h-screen + overflow-hidden: the whole page is one screen, never scrolls.
    <div className="grid h-screen overflow-hidden lg:grid-cols-[minmax(0,380px)_1fr]">
      {/* Sign-in */}
      <div className="flex flex-col justify-center border-r border-line bg-white px-7 py-8">
        <div className="mx-auto w-full max-w-sm">
          <div className="mb-6 flex items-center gap-3">
            <span className="flex h-9 w-9 items-center justify-center rounded border border-navy-700 bg-navy-800 text-white">
              <svg viewBox="0 0 24 24" className="h-4.5 w-4.5" fill="none" aria-hidden>
                <path
                  d="M12 3 4 6.2v5.3c0 4.6 3.2 8.5 8 9.5 4.8-1 8-4.9 8-9.5V6.2L12 3Z"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  strokeLinejoin="round"
                />
                <circle cx="12" cy="11" r="2.4" stroke="currentColor" strokeWidth="1.5" />
              </svg>
            </span>
            <div>
              <div className="text-base font-semibold tracking-wide text-ink-900">SENTINEL</div>
              <div className="text-2xs uppercase tracking-wider text-ink-500">
                CCTV Asset Registry
              </div>
            </div>
          </div>

          <h1 className="text-lg font-semibold text-ink-900">Sign in</h1>
          <p className="mt-1 text-[13px] text-ink-500">
            Your role decides which records you may read and whether you may view footage.
          </p>

          <form className="mt-5 space-y-3" onSubmit={submit}>
            <div>
              <label className="field-label" htmlFor="username">
                Username
              </label>
              <input
                id="username"
                className="input mt-1"
                autoComplete="username"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                required
              />
            </div>
            <div>
              <label className="field-label" htmlFor="password">
                Password
              </label>
              <input
                id="password"
                type="password"
                className="input mt-1"
                autoComplete="current-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                required
              />
            </div>

            {error && <Notice tone="bad">{error}</Notice>}

            <button className="btn btn-primary w-full" type="submit" disabled={busy}>
              {busy && <Spinner />}
              {busy ? "Signing in…" : "Sign in"}
            </button>
          </form>

          <p className="mt-5 border-t border-line pt-3 text-2xs leading-relaxed text-ink-400">
            Footage is brokered only for cameras the owning department has enabled, only for
            accounts whose department, city and zone match, and only through short-lived audited
            sessions. All data in this build is synthetic.
          </p>
        </div>
      </div>

      {/* Accounts - username and Use only */}
      <div className="hidden flex-col justify-center bg-paper px-7 py-8 lg:flex">
        <div className="mx-auto w-full max-w-3xl">
          <h2 className="text-[13px] font-semibold text-ink-900">Demonstration accounts</h2>
          <p className="mt-0.5 text-2xs text-ink-500">
            Select one to fill the form. Grouped by what the account is for.
          </p>

          <div className="mt-3 space-y-2.5">
            {GROUPS.map((section) => (
              <section key={section.group} className="card overflow-hidden">
                <header className="border-b border-line bg-[#eef1f5] px-3 py-1.5">
                  <h3 className="text-2xs font-semibold uppercase tracking-wider text-ink-500">
                    {section.group}
                  </h3>
                </header>
                <div className="grid grid-cols-2 gap-x-3 p-2 xl:grid-cols-3">
                  {section.accounts.map((account) => (
                    <div
                      key={account.username}
                      className="flex items-center justify-between gap-2 rounded px-1.5 py-1 hover:bg-brand-50"
                    >
                      <span className="mono truncate">{account.username}</span>
                      <button
                        className="btn btn-sm shrink-0"
                        type="button"
                        onClick={() => use(account)}
                      >
                        Use
                      </button>
                    </div>
                  ))}
                </div>
              </section>
            ))}
          </div>
        </div>
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
