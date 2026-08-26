/**
 * Browser-side client for the Sentinel registry.
 *
 * Every call goes to /api/sentinel/... on this origin, which the Next.js server
 * proxies using an httpOnly session cookie. Nothing here knows a department
 * credential, an internal hostname, or a video URL - because none exists.
 */
import type {
  AccessPolicy,
  DetectorHealth,
  AuditEntry,
  Camera,
  CameraDetail,
  CameraHealthRecord,
  CorrelationResult,
  Detection,
  EventRecord,
  GapAnalysis,
  GrantStatus,
  InstallationRequest,
  Operator,
  Overview,
  PagedCameras,
  PlatformHealth,
  SourceSystem,
  SyncResponse,
  VideoAccessRequest,
  VideoMode,
  VideoSession,
} from "./types";

const BASE = "/api/sentinel";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, { ...init, cache: "no-store" });

  if (response.status === 401 && typeof window !== "undefined") {
    window.location.href = `/login?next=${encodeURIComponent(window.location.pathname)}`;
    throw new ApiError("Session expired", 401);
  }

  if (!response.ok) {
    let detail: unknown;
    try {
      detail = (await response.json())?.detail;
    } catch {
      detail = await response.text().catch(() => undefined);
    }
    throw new ApiError(describe(detail, response.status), response.status, detail);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/** Turn a central API error body into one line an operator can act on. */
function describe(detail: unknown, status: number): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    // FastAPI validation errors
    const first = detail[0] as { loc?: unknown[]; msg?: string } | undefined;
    if (first?.msg) {
      const field = Array.isArray(first.loc) ? first.loc.slice(-1)[0] : undefined;
      return field ? `${field}: ${first.msg}` : first.msg;
    }
  }
  if (detail && typeof detail === "object") {
    const record = detail as Record<string, unknown>;
    if (typeof record.message === "string") {
      const source = typeof record.source_system === "string" ? ` (${record.source_system})` : "";
      return `${record.message}${source}`;
    }
    if (typeof record.code === "string") return String(record.code);
  }
  return `Request failed with HTTP ${status}`;
}

function query(params: Record<string, string | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value) search.set(key, value);
  }
  const encoded = search.toString();
  return encoded ? `?${encoded}` : "";
}

function post<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
}

export const api = {
  // --- platform ---------------------------------------------------------
  health: () => request<PlatformHealth>("/health"),
  me: () => request<Operator>("/api/v1/auth/me"),
  overview: () => request<Overview>("/api/v1/overview"),
  sources: () => request<SourceSystem[]>("/api/v1/sources"),
  syncAll: () => post<SyncResponse>("/api/v1/sources/sync"),

  // --- registry ---------------------------------------------------------
  cameras: (filters: {
    source_system?: string;
    owning_department?: string;
    district?: string;
    status?: string;
    installation_status?: string;
    q?: string;
  }): Promise<Camera[]> =>
    // Unwrap the envelope here rather than at every call site. The generic on
    // `request` is an unchecked assertion, so claiming `Camera[]` for a paged
    // body compiles cleanly and then throws in the browser.
    request<PagedCameras>(`/api/v1/cameras${query(filters)}`).then(
      (page) => page.items ?? [],
    ),

  camera: (cameraId: string) =>
    request<CameraDetail>(`/api/v1/cameras/${encodeURIComponent(cameraId)}`),

  cameraHealth: (cameraId: string) =>
    request<CameraHealthRecord>(`/api/v1/cameras/${encodeURIComponent(cameraId)}/health`),

  accessPolicy: (cameraId: string) =>
    request<AccessPolicy>(`/api/v1/cameras/${encodeURIComponent(cameraId)}/access-policy`),

  // --- installation onboarding -----------------------------------------
  installationRequests: (filters: { status?: string; owning_department?: string } = {}) =>
    request<InstallationRequest[]>(`/api/v1/installation-requests${query(filters)}`),

  installationRequest: (requestId: string) =>
    request<InstallationRequest>(`/api/v1/installation-requests/${encodeURIComponent(requestId)}`),

  createInstallationRequest: (form: Record<string, unknown>) =>
    post<InstallationRequest>("/api/v1/installation-requests", { form }),

  updateInstallationRequest: (requestId: string, form: Record<string, unknown>) =>
    request<InstallationRequest>(
      `/api/v1/installation-requests/${encodeURIComponent(requestId)}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ form }),
      },
    ),

  submitRequest: (requestId: string) =>
    post<InstallationRequest>(
      `/api/v1/installation-requests/${encodeURIComponent(requestId)}/submit`,
    ),

  // -- analytics ----------------------------------------------------------
  // What the edge workers saw. Scoped to the owning department: a detection
  // describes a place at a time, so it follows the video rules, not the
  // registry ones.

  detections: (filters: {
    camera_id?: string;
    class_name?: string;
    min_confidence?: string;
    since_hours?: string;
    limit?: string;
  } = {}) => request<Detection[]>(`/api/v1/detections${query(filters)}`),

  detectorHealth: () => request<DetectorHealth>("/api/v1/detector/health"),

  // -- authorised viewing -------------------------------------------------

  openVideoSession: (body: {
    camera_id: string;
    mode: VideoMode;
    reason: string;
    /**
     * Re-entered at the moment of viewing. Passed straight through and never
     * stored, never put in component state that outlives the request, and
     * never written to the URL.
     */
    password: string;
    case_id?: string;
    start_time_utc?: string;
    end_time_utc?: string;
  }) => post<VideoSession>("/api/v1/video-sessions", body),

  videoSessionStatus: (sessionId: string) =>
    request<VideoSession>(
      `/api/v1/video-sessions/${encodeURIComponent(sessionId)}/status`,
    ),

  closeVideoSession: (sessionId: string) =>
    request<void>(`/api/v1/video-sessions/${encodeURIComponent(sessionId)}`, {
      method: "DELETE",
    }),

  /**
   * The URL to put in a <video src>. It points at this origin's proxy, which
   * attaches the session cookie server-side - the browser never holds a token
   * and never learns the central API's address.
   */
  streamUrl: (session: VideoSession) => `/api/sentinel${session.stream_url}`,

  // -- video access requests ---------------------------------------------
  // Metadata federates on its own; footage is asked for. These four calls are
  // that conversation.

  listAccessRequests: (status?: GrantStatus) =>
    request<VideoAccessRequest[]>(
      `/api/v1/video-access-requests${status ? `?request_status=${status}` : ""}`,
    ),

  requestVideoAccess: (body: {
    camera_id: string;
    reason: string;
    case_id?: string;
    modes?: string[];
  }) => post<VideoAccessRequest>("/api/v1/video-access-requests", body),

  decideVideoAccess: (
    grantId: string,
    verdict: "grant" | "deny",
    body: { note?: string; modes?: string[]; days?: number } = {},
  ) =>
    post<VideoAccessRequest>(
      `/api/v1/video-access-requests/${encodeURIComponent(grantId)}/${verdict}`,
      body,
    ),

  revokeVideoAccess: (grantId: string) =>
    request<VideoAccessRequest>(
      `/api/v1/video-access-requests/${encodeURIComponent(grantId)}`,
      { method: "DELETE" },
    ),

  suspendRequest: (requestId: string, reason: string) =>
    post<InstallationRequest>(
      `/api/v1/installation-requests/${encodeURIComponent(requestId)}/suspend`,
      { reason },
    ),

  decommissionRequest: (requestId: string, reason: string) =>
    post<InstallationRequest>(
      `/api/v1/installation-requests/${encodeURIComponent(requestId)}/decommission`,
      { reason },
    ),

  syncRequestMetadata: (requestId: string) =>
    post<InstallationRequest>(
      `/api/v1/installation-requests/${encodeURIComponent(requestId)}/sync-metadata`,
    ),

  // --- events -----------------------------------------------------------
  events: (params: {
    camera_id?: string;
    source_system?: string;
    event_type?: string;
    since_hours?: string;
    refresh?: string;
    limit?: string;
  } = {}) => request<EventRecord[]>(`/api/v1/events${query(params)}`),

  correlation: (params: {
    window_seconds?: string;
    radius_m?: string;
    since_hours?: string;
    refresh?: string;
  } = {}) => request<CorrelationResult>(`/api/v1/events/correlation${query(params)}`),

  // --- reports ----------------------------------------------------------
  gapAnalysis: (params: { min_cameras_per_district?: string; ageing_years?: string } = {}) =>
    request<GapAnalysis>(`/api/v1/reports/gap-analysis${query(params)}`),

  // --- oversight --------------------------------------------------------
  audit: (filters: { action?: string; resource_id?: string; outcome?: string; limit?: string } = {}) =>
    request<AuditEntry[]>(`/api/v1/audit${query(filters)}`),
};

export async function signIn(username: string, password: string): Promise<Operator> {
  const response = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
    cache: "no-store",
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new ApiError(describe(body?.detail, response.status), response.status, body?.detail);
  }
  return body.user as Operator;
}

export async function signOut(): Promise<void> {
  await fetch("/api/auth/logout", { method: "POST", cache: "no-store" });
}
