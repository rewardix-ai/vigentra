/**
 * Route guard.
 *
 * Anything outside the sign-in page requires a session cookie. This is a
 * convenience redirect only - the central API independently authenticates and
 * authorises every request, so a forged cookie gets an operator nowhere.
 */
import { NextRequest, NextResponse } from "next/server";

const PUBLIC_PATHS = ["/login", "/api/auth/login"];

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  if (PUBLIC_PATHS.some((path) => pathname === path || pathname.startsWith(`${path}/`))) {
    return NextResponse.next();
  }

  if (!request.cookies.get("sentinel_session")?.value) {
    // An API call has to fail as JSON. Redirecting one sends the browser to the
    // sign-in *page*, which it follows transparently and which answers 200 with
    // HTML - so the caller's `response.json()` dies on "<!DOCTYPE" and the
    // operator is shown a parser error instead of "your session expired". The
    // browser client turns this 401 into a sign-in redirect that remembers the
    // page it was on.
    if (pathname.startsWith("/api/")) {
      return NextResponse.json({ detail: "Not signed in" }, { status: 401 });
    }
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    url.search = pathname === "/" ? "" : `?next=${encodeURIComponent(pathname)}`;
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

/**
 * Exclude every asset path the browser needs before it can render the page:
 *   `_next/*`  - all Next.js build output (JS, CSS, data, HMR)
 *   `favicon`  - browser icon
 *   any `*.something` request - CSS, JS, fonts, images served by the runtime
 *
 * `/api/auth/*` is handled inside the middleware itself so `/api/auth/logout`
 * stays reachable after sign-out.
 */
export const config = {
  matcher: ["/((?!_next|favicon.ico|.*\\..*).*)"],
};
