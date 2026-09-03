/**
 * Sign-in. Runs only on the Next.js server.
 *
 * The bearer token is placed in an httpOnly cookie, so page scripts never hold
 * a Vigentra credential or token: an XSS on this dashboard cannot read it, and
 * it is attached to upstream calls by the proxy route rather than by the page.
 */
import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const CENTRAL_API_URL = process.env.CENTRAL_API_URL ?? "http://central-api:8000";
// Route files may only export handlers, so keep the cookie name a local constant.
const SESSION_COOKIE = "vigentra_session";

export async function POST(request: NextRequest): Promise<NextResponse> {
  let credentials: { username?: string; password?: string };
  try {
    credentials = await request.json();
  } catch {
    return NextResponse.json({ detail: "Malformed sign-in request" }, { status: 400 });
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${CENTRAL_API_URL}/api/v1/auth/login`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        // Preserve the operator's address for the central audit trail.
        "X-Forwarded-For":
          request.headers.get("x-forwarded-for") ?? request.headers.get("x-real-ip") ?? "",
      },
      body: JSON.stringify({
        username: credentials.username ?? "",
        password: credentials.password ?? "",
      }),
      cache: "no-store",
    });
  } catch (error) {
    return NextResponse.json(
      {
        detail: "Central registry is unreachable from the dashboard server",
        error: error instanceof Error ? error.message : String(error),
      },
      { status: 502 },
    );
  }

  const body = await upstream.json().catch(() => ({}));
  if (!upstream.ok) {
    return NextResponse.json(
      { detail: body?.detail ?? "Sign-in failed" },
      { status: upstream.status },
    );
  }

  const response = NextResponse.json({ user: body.user });
  response.cookies.set({
    name: SESSION_COOKIE,
    value: body.access_token,
    httpOnly: true,
    sameSite: "lax",
    path: "/",
    // The demo stack is served over plain HTTP on localhost, where a Secure
    // cookie would simply never be stored. Set VIGENTRA_SECURE_COOKIE=1 when
    // deploying behind TLS.
    secure: process.env.VIGENTRA_SECURE_COOKIE === "1",
    maxAge: Math.max(60, Number(body.expires_in ?? 3600)),
  });
  return response;
}
