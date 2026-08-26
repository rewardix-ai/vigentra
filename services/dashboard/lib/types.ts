/**
 * Canonical types mirroring services/central-api/app/schemas.py.
 *
 * Metadata only. There is deliberately no VideoSession type, no stream URL and
 * no media token anywhere in this file - the API has none to send.
 */

export type CameraHealthStatus = "online" | "offline" | "degraded" | "unavailable" | "unknown";
export type InstallationStatus = "COMMISSIONED" | "SUSPENDED" | "DECOMMISSIONED";
export type SyncStatus = "SYNCHRONIZED" | "PENDING" | "WITHDRAWN" | "FAILED";

// No approval states: a form that passes its own department's validation is
// REGISTERED on the spot and appears in the central registry. Footage is what
// still needs a human decision - see VideoAccessRequest below.
export type RequestStatus =
  | "DRAFT"
  | "SUBMITTED"
  | "VALIDATION_FAILED"
  | "REGISTERED"
  | "SYNCHRONIZED"
  | "SUSPENDED"
  | "DECOMMISSIONED";

export type VideoAccessState =
  | "live_and_playback"
  | "live_only"
  | "playback_only"
  | "denied"
  | "not_enabled_by_owner"
  | "needs_unit_approval"
  | "camera_unavailable";

export type DetectionClass =
  | "person"
  | "car"
  | "motorcycle"
  | "bus"
  | "truck"
  | "auto-rickshaw"
  | "bicycle";

/** One object seen in one frame, as recorded by an edge worker. */
export interface Detection {
  detection_id: string;
  camera_id: string;
  camera_name: string | null;
  owning_department: string | null;
  city: string | null;
  timestamp_utc: string;
  class_name: DetectionClass | string;
  class_id: number;
  confidence: number;
  bbox_xyxy: number[];
  model_name: string;
  model_version: string;
  source_mode: string;
  inference_latency_ms: number | null;
  frame_quality: string | null;
  evidence_reference: string | null;
  is_demo_data: boolean;
  provenance: Record<string, unknown>;
  created_at: string | null;
  /** Registration number, if ANPR ran and this account holds `plate:read`. */
  plate_text: string | null;
  plate_confidence: number | null;
  plate_bbox_xyxy: number[] | null;
  plate_reader: string | null;
  /** True when a plate exists but is withheld from this reader. */
  plate_withheld: boolean;
}

/** What the platform is configured to run, and what has actually reported. */
export interface DetectorHealth {
  enabled: boolean;
  detector: string;
  model_name: string;
  model_version: string | null;
  device: string;
  weights_available: boolean;
  classes: string[];
  confidence_threshold: number;
  frame_sample_interval: number;
  detail: string;
  accuracy_disclaimer: string;
}

export type VideoMode = "live" | "playback";

/**
 * An authorised viewing session. `stream_url` is a Sentinel-owned opaque path
 * - never a department address, a ticket or a credential.
 */
export interface VideoSession {
  session_id: string;
  camera_id: string;
  camera_name: string | null;
  department: string;
  city: string | null;
  mode: VideoMode;
  stream_url: string;
  /**
   * How the bytes behind `stream_url` are packaged. `hls` is a live playlist
   * and needs hls.js outside Safari; `http-mp4` plays natively.
   */
  stream_protocol: "hls" | "http-mp4" | "mock-file" | string;
  status: string;
  expires_at_utc: string;
  expires_in_seconds: number;
  watermark: string;
  case_id: string | null;
  reason: string | null;
  audit_id: string | null;
  start_time_utc: string | null;
  end_time_utc: string | null;
  access_count: number;
  /** Which part of the recording answers the requested window (playback only). */
  segment_start_seconds: number | null;
  segment_end_seconds: number | null;
}

export type GrantStatus = "requested" | "granted" | "denied" | "revoked" | "expired";

export interface VideoAccessRequest {
  grant_id: string;
  camera_id: string;
  camera_name: string | null;
  owning_department: string;
  requested_by: string;
  requester_department: string;
  requester_role: string;
  reason: string;
  case_id: string | null;
  status: GrantStatus;
  allowed_modes: string[];
  decided_by: string | null;
  decided_at: string | null;
  decision_note: string | null;
  requested_at: string;
  expires_at: string | null;
  is_active: boolean;
}

export type SourceTypeValue = "RTSP" | "ONVIF" | "VMS_API" | "NVR" | "other";

export type CameraTypeValue =
  | "fixed"
  | "PTZ"
  | "dome"
  | "bullet"
  | "ANPR-capable"
  | "thermal"
  | "other";

export type PurposeValue =
  | "traffic monitoring"
  | "public safety"
  | "junction monitoring"
  | "highway monitoring"
  | "other";

export type LocalRoleValue =
  | "department_operator"
  | "district_supervisor"
  | "state_supervisor"
  | "investigator"
  | "system_admin"
  | "auditor";

export type AttachmentTypeValue =
  | "site_survey"
  | "installation_certificate"
  | "camera_photograph"
  | "approval_document";

export interface AttachmentRef {
  document_type: AttachmentTypeValue;
  reference: string;
  filename?: string | null;
  custodian?: string | null;
}

export interface CameraLocation {
  district: string;
  road_or_junction: string | null;
  landmark: string | null;
  latitude: number | null;
  longitude: number | null;
  view_direction: string;
}

export interface TechnicalSummary {
  resolution: string | null;
  fps: number | null;
  codec: string | null;
  timezone: string;
  source_type: SourceTypeValue | null;
  retention_days: number | null;
}

export interface InstallationSummary {
  installation_date: string | null;
  commissioning_date: string | null;
  installation_status: InstallationStatus;
  installation_request_id: string | null;
}

export interface ApprovalSummary {
  status: RequestStatus;
  /** Retained for records registered before the approval stage was removed. */
  approved_by_role: string | null;
  approved_at: string | null;
}

export interface AccessPolicySummary {
  local_video_access: boolean;
  /** Whether the owning unit permits Sentinel to broker this camera at all. */
  sentinel_video_access: boolean;
  permitted_local_roles: string[];
  footage_custodian: string;
  metadata_visibility_level: string;
  policy_version: number;
}

export interface CameraHealthSummary {
  status: CameraHealthStatus;
  last_heartbeat_utc: string | null;
  last_frame_utc: string | null;
  last_metadata_sync_utc: string | null;
  latency_ms?: number | null;
  reconnect_count?: number | null;
}

export interface SentinelSyncSummary {
  status: SyncStatus;
  synced_at_utc: string | null;
  source_request_id: string | null;
}

export interface Camera {
  camera_id: string;
  external_camera_id: string;
  source_system: string;
  owning_department: string;
  owning_unit: string | null;
  name: string;
  vendor: string | null;
  model: string | null;
  camera_type: CameraTypeValue;
  installation_purpose: PurposeValue | null;
  location: CameraLocation;
  source_type: SourceTypeValue | null;
  vms_name: string | null;
  technical_summary: TechnicalSummary;
  installation: InstallationSummary;
  approval: ApprovalSummary;
  access_policy_summary: AccessPolicySummary;
  health: CameraHealthSummary;
  sentinel_sync: SentinelSyncSummary;
  capabilities?: string[];
  is_demo_data?: boolean;
  /**
   * What THIS reader may do with this camera's footage, computed per request.
   * The same camera reads "live_and_playback" for its owning unit and
   * "needs_unit_approval" for everyone else — which is a prompt to ask, not a
   * refusal.
   */
  video_access: VideoAccessState;
  video_access_reason: string | null;
  footage_access_via_sentinel?: boolean;
}

/**
 * What `GET /api/v1/cameras` actually returns. The endpoint wraps its rows in
 * an envelope carrying the applied filters and the pre-pagination total, so a
 * caller that treats the response as a bare array gets an object and fails on
 * the first `.map`.
 */
export interface PagedCameras {
  items: Camera[];
  filters: Record<string, string>;
  total: number;
}

export interface CameraDetail extends Camera {
  camera_serial_masked: string | null;
  police_station_or_zone: string | null;
  maintenance_agency: string | null;
  installation_vendor: string | null;
  coverage_description: string | null;
  entry_exit_zone_description: string | null;
  address_or_landmark: string | null;
  attachments: AttachmentRef[];
  provenance: Record<string, unknown>;
  first_synced_at: string | null;
  last_synced_at: string | null;
  visibility_level: string;
  /** Field names withheld from this role, so the UI can say so explicitly. */
  redacted_fields: string[];
}

export interface CameraHealthRecord extends CameraHealthSummary {
  camera_id: string;
  source_system: string;
  owning_department: string;
  detail: Record<string, unknown>;
  checked_at: string | null;
}

export interface AccessPolicy {
  camera_id: string;
  camera_name: string;
  owning_department: string;
  source_system: string;
  sentinel_metadata_access: string;
  sentinel_video_access: boolean;
  sentinel_video_access_note: string;
  local_video_access_enabled: boolean;
  permitted_local_roles: string[];
  footage_custodian: string;
  local_vms_name: string | null;
  policy_version: number;
  approved_by_role: string | null;
  approved_at: string | null;
}

export interface InstallationRequest {
  request_id: string;
  source_system: string;
  owning_department: string;
  owning_unit: string | null;
  status: RequestStatus;
  camera_name: string | null;
  external_camera_id: string | null;
  district: string | null;
  created_by: string | null;
  created_at: string | null;
  submitted_by: string | null;
  submitted_at: string | null;
  approved_by: string | null;
  approved_by_role: string | null;
  approved_at: string | null;
  rejected_by: string | null;
  rejected_at: string | null;
  rejection_reason: string | null;
  withdrawal_reason: string | null;
  synchronized_at: string | null;
  updated_at: string | null;
  validation_errors: string[];
  form: Record<string, unknown>;
  attachments: AttachmentRef[];
  sentinel_video_access: boolean;
}

export interface SourceSystem {
  source_system: string;
  display_name: string;
  department: string;
  adapter: string;
  adapter_version: string;
  status: string;
  camera_count: number;
  pending_requests: number;
  endpoint: string;
  last_sync_at: string | null;
  last_success_at: string | null;
  last_error: string | null;
  latency_ms: number | null;
}

export interface SyncSourceResult {
  approved_records_seen: number;
  synchronized: number;
  skipped_unregistered: number;
  withdrawn: number;
  created: number;
  updated: number;
  errors: string[];
  latency_ms: number | null;
  status: string;
}

export interface SyncResponse {
  success: boolean;
  metadata_only: boolean;
  sources: Record<string, SyncSourceResult>;
  total_cameras: number;
  synced_at: string;
}

export interface Overview {
  total_cameras: number;
  approved_cameras: number;
  suspended_cameras: number;
  decommissioned_cameras: number;
  pending_installation_requests: number;
  draft_installation_requests: number;
  requiring_metadata_review: number;
  online: number;
  offline: number;
  degraded: number;
  unavailable: number;
  unknown: number;
  departments_connected: number;
  departments_total: number;
  last_metadata_sync_at: string | null;
  video_access: string;
}

export interface EventRecord {
  event_id: string;
  source_system: string;
  external_event_id: string;
  camera_id: string;
  event_type: string;
  severity: string | null;
  timestamp_utc: string;
  payload: Record<string, unknown>;
  provenance: Record<string, unknown>;
  camera_name: string | null;
  owning_department: string | null;
  district: string | null;
  ingested_at: string | null;
}

export interface CorrelationPair {
  a: EventRecord;
  b: EventRecord;
  distance_m: number;
  delta_seconds: number;
  cross_department: boolean;
}

export interface CorrelationResult {
  window_seconds: number;
  radius_m: number;
  considered: number;
  pairs: CorrelationPair[];
}

export interface DistrictCoverage {
  district: string;
  cameras: number;
  online: number;
  degraded: number;
  offline: number;
  unavailable: number;
  by_department: Record<string, number>;
  is_thin: boolean;
}

export interface AgeingCamera {
  camera_id: string;
  name: string;
  owning_department: string;
  district: string;
  installation_date: string | null;
  age_years: number | null;
  health_status: CameraHealthStatus;
}

export interface GapAnalysis {
  generated_at: string;
  thresholds: { min_cameras_per_district: number; ageing_years: number };
  totals: {
    cameras: number;
    active_cameras: number;
    districts_covered: number;
    thin_districts: number;
    ageing_cameras: number;
  };
  districts_covered: DistrictCoverage[];
  thin_districts: DistrictCoverage[];
  ageing_cameras: AgeingCamera[];
  departments_without_coverage: string[];
}

export interface AuditEntry {
  audit_id: string;
  timestamp_utc: string;
  username: string;
  role: string;
  action: string;
  outcome: string;
  resource_type: string | null;
  resource_id: string | null;
  department: string | null;
  source_system: string | null;
  case_or_reason: string | null;
  client_ip: string | null;
  details: Record<string, unknown>;
}

export interface DependencyHealth {
  name: string;
  status: string;
  detail: string | null;
  latency_ms: number | null;
}

export interface PlatformHealth {
  status: string;
  service: string;
  version: string;
  environment: string;
  module: string;
  access_model: string;
  video_access: string;
  time_utc: string;
  dependencies: DependencyHealth[];
}

export interface Operator {
  username: string;
  display_name: string;
  role: string;
  department: string;
  unit: string | null;
  permissions: string[];
  visibility_level: string;
  sentinel_video_access: boolean;
}

/** Canonical installation form as posted to the API. */
export interface InstallationFormValues {
  camera_name: string;
  external_camera_id: string;
  camera_serial_number: string;
  camera_vendor: string;
  camera_model: string;
  camera_type: CameraTypeValue;
  installation_purpose: PurposeValue;

  owning_department: string;
  owning_unit: string;
  district: string;
  police_station_or_zone: string;
  local_admin_contact: string;
  maintenance_agency: string;
  installation_vendor: string;

  latitude: string;
  longitude: string;
  address_or_landmark: string;
  road_or_junction: string;
  view_direction: string;
  coverage_description: string;
  entry_exit_zone_description: string;

  source_type: SourceTypeValue;
  vms_name: string;
  vms_vendor: string;
  resolution: string;
  fps: string;
  codec: string;
  supports_live: boolean;
  supports_playback: boolean;
  retention_days: string;
  timezone: string;
  installation_date: string;
  commissioning_date: string;

  permitted_local_roles: LocalRoleValue[];
  attachments: AttachmentRef[];
}
