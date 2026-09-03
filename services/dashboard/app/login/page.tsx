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
import { BrandLockup, TAGLINE } from "@/components/Brand";

interface DemoAccount {
  username: string;
  /** Filled by the Use button, and shown beside the username. */
  password: string;
}

/**
 * Demo accounts, grouped by purpose.
 *
 * Both halves of the credential are shown. These are synthetic accounts on a
 * demo build and the passwords already ship inside this client bundle, so
 * hiding them on screen bought no secrecy - it only meant anyone running the
 * demo had to be told them out of band. The grouping stays because it IS the
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
      // joint.control first: the only account that can watch every camera in
      // the estate, so it is the one to open the live wall with. The two
      // statewide control rooms next - between them they cover the same set,
      // one department each.
      { username: "joint.control", password: "Joint@2026" },
      { username: "traffic.state", password: "Traffic@2026" },
      { username: "municipal.state", password: "Municipal@2026" },
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
      { username: "traffic.ai", password: "AiOps@2026" },
      { username: "municipal.ai", password: "MuniOps@2026" },
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
    // Two panes side by side on a wide screen, one screen tall and never
    // scrolling. Below that they stack and the page scrolls, so the account
    // list stays reachable on a narrow window rather than being hidden.
    <div className="grid min-h-screen lg:h-screen lg:overflow-hidden lg:grid-cols-[minmax(0,380px)_1fr]">
      {/* Sign-in */}
      <div className="flex flex-col justify-center border-r border-line bg-white px-7 py-8">
        <div className="mx-auto w-full max-w-sm">
          <div className="mb-6">
            <BrandLockup subtitle={null} />
            <p className="mt-2.5 text-[13px] leading-snug text-ink-500">{TAGLINE}</p>
          </div>

          <h1 className="text-lg font-semibold text-ink-900">Sign in</h1>
          <p className="mt-1 text-[13px] text-ink-500">
            Your role decides which records you may read and whether you may view footage.
          </p>

          <form className="mt-5 space-y-3" onSubmit={submit}>
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

          <p className="mt-5 border-t border-line pt-3 text-2xs leading-relaxed text-ink-400">
            Footage is brokered only for cameras the owning department has enabled, only for
            accounts whose department, city and zone match, and only through short-lived audited
            sessions. All data in this build is synthetic.
          </p>
        </div>
      </div>

      {/* Accounts - username, password and Use */}
      <div className="flex flex-col justify-center bg-paper px-7 py-8">
        <div className="mx-auto w-full max-w-3xl">
          <Capabilities />

          <h2 className="mt-5 text-[13px] font-semibold text-ink-900">Demonstration accounts</h2>
          <p className="mt-0.5 text-2xs text-ink-500">
            Select one to fill the form, or read the credential off the list.
            Grouped by what the account is for.
          </p>

          <div className="mt-3 space-y-2.5">
            {GROUPS.map((section) => (
              <section key={section.group} className="card overflow-hidden">
                <header className="border-b border-line bg-[#eef1f5] px-3 py-1.5">
                  <h3 className="text-2xs font-semibold uppercase tracking-wider text-ink-500">
                    {section.group}
                  </h3>
                </header>
                <div className="grid grid-cols-1 gap-x-3 p-2 md:grid-cols-2 xl:grid-cols-3">
                  {section.accounts.map((account) => (
                    <div
                      key={account.username}
                      className="flex items-center justify-between gap-2 rounded px-1.5 py-1 hover:bg-brand-50"
                    >
                      <span className="mono min-w-0 truncate">
                        {account.username}
                        <span className="ml-1.5 text-ink-500">{account.password}</span>
                      </span>
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

/**
 * What the platform does, before anyone signs in.
 *
 * The sign-in screen is the first thing an evaluator sees and it used to say
 * only "Sign in" and list credentials - a console with no statement of what it
 * is. These four are the capabilities the build actually ships, named the way
 * the brief names them, so the reader can match each to a screen in the rail
 * once they are through.
 */
const CAPABILITIES: { title: string; body: string }[] = [
  {
    title: "Federated registry + GIS",
    body:
      "One canonical register over independently owned Traffic Police and Municipal "
      + "Corporation estates, plotted with coverage direction on an interactive map.",
  },
  {
    title: "Brokered live video",
    body:
      "Short-lived watermarked sessions against an opaque stream id. Upstream URLs and "
      + "credentials never leave the server, and every view is audited.",
  },
  {
    title: "Edge ANPR + vehicle counts",
    body:
      "Detection and plate reading run at the edge and stream in continuously, so counts "
      + "and class breakdowns accumulate per camera and survive a reload.",
  },
  {
    title: "Watchlist + cross-camera tracing",
    body:
      "A plate of interest raises an alert on sighting, and a vehicle's movement can be "
      + "assembled across cameras - each disclosure gated by permission and reason.",
  },
];

function Capabilities() {
  return (
    <section>
      <h2 className="text-[13px] font-semibold text-ink-900">{TAGLINE}</h2>
      <p className="mt-0.5 text-2xs text-ink-500">
        Gujarat Police CCTV Integration Hackathon 2026 · all data in this build is synthetic.
      </p>
      <div className="mt-2.5 grid gap-2 sm:grid-cols-2">
        {CAPABILITIES.map((capability) => (
          <div key={capability.title} className="card px-3 py-2.5">
            <div className="text-2xs font-semibold uppercase tracking-wider text-brand-700">
              {capability.title}
            </div>
            <p className="mt-1 text-2xs leading-relaxed text-ink-500">{capability.body}</p>
          </div>
        ))}
      </div>
    </section>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={<div className="p-8 text-[13px] text-ink-500">Loading…</div>}>
      <SignInForm />
    </Suspense>
  );
}
