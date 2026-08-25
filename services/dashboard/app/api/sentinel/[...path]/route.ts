/**
 * Server-side proxy to the Sentinel central registry.
 *
 * The browser calls /api/sentinel/... on its own origin. This handler - which
 * runs only on the Next.js server - reads the httpOnly session cookie and
 * attaches it as a bearer token upstream. Page scripts therefore never hold a
 * token, and the central API's URL is never exposed to the client.
 *
 * It forwards JSON *and* video. The distinction matters: an earlier version
 * returned `await upstream.text()`, which decodes the body as UTF-8. That is
 * lossless for JSON and destroys anything binary - every byte above 0x7F was
 * re-encoded, inflating a 6.7 MB clip to 12.3 MB of unplayable rubbish. The
 * body is now piped through untouched, and Range is forwarded in both
 * directions so the player can seek.
 */
import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const CENTRAL_API_URL = process.env.CENTRAL_API_URL ?? "http://central-api:8000";
const SESSION_COOKIE = "sentinel_session";

/** Response headers safe to hand back to the browser. */
const PASSTHROUGH = new Set([
  "content-type",
  // Media needs all four of these, or the browser cannot size the resource,
  // cannot seek, and silently refuses to play.
  "content-length",
  "content-range",
  "accept-ranges",
  "content-disposition",
  "x-sentinel-degraded",
  "x-sentinel-access-model",
  "x-sentinel-video-access",
]);

async function handler(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
): Promise<Response> {
  const { path } = await context.params;
  const token = request.cookies.get(SESSION_COOKIE)?.value;

  if (!token) {
    return NextResponse.json({ detail: "Not signed in" }, { status: 401 });
  }

  const headers = new Headers({
    Authorization: `Bearer ${token}`,
    "X-Forwarded-For":
      request.headers.get("x-forwarded-for") ?? request.headers.get("x-real-ip") ?? "",
  });

  // Scrubbing happens upstream; what the player asks for has to reach it, or
  // every seek returns the whole file from byte zero.
  const range = request.headers.get("range");
  if (range) headers.set("Range", range);

  let body: string | undefined;
  if (request.method !== "GET" && request.method !== "HEAD") {
    body = await request.text();
    headers.set("Content-Type", "application/json");
    if (!body) body = "{}";
  }

  const target = `${CENTRAL_API_URL}/${path.join("/")}${new URL(request.url).search}`;

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers,
      body,
      cache: "no-store",
      // Node's fetch buffers the whole body by default; for a video that is
      // both slow to start and wasteful of server memory.
      // @ts-expect-error - undici-only option, not in the DOM fetch types.
      duplex: "half",
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

  const responseHeaders = new Headers();
  upstream.headers.forEach((value, key) => {
    if (PASSTHROUGH.has(key.toLowerCase())) responseHeaders.set(key, value);
  });
  responseHeaders.set("Cache-Control", "no-store");

  // Pipe the raw stream. Never .text() and never .json() - the first corrupts
  // binary, the second throws on it.
  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders,
  });
}

export { handler as GET, handler as POST, handler as PATCH, handler as DELETE };
