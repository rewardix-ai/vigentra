/** Sign out by clearing the session cookie. */
import { NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(): Promise<NextResponse> {
  const response = NextResponse.json({ signed_out: true });
  response.cookies.set({ name: "sentinel_session", value: "", path: "/", maxAge: 0 });
  return response;
}
