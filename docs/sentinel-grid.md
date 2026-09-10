# The Sentinel camera grid

The two department systems in this repository are mocks. The **Sentinel grid**
is not: it is the live sandbox published at
<https://sentinel.gujarat.gov.in/resource> and served from `live.corp8.cloud`,
federated through the same adapter contract as everything else.

30 cameras, across Ahmedabad, Junagadh, Navsari, Rajkot, Patan, Gandhinagar,
Bhavnagar, Banaskantha and Kutch.

---

## What it is, and what it is not

The grid publishes a catalogue and live streams. It has **no installation
register**, **no recorded archive**, and its own rules say *"consume only — do
not push streams to any path, and do not call the gateway's control API."*

Two consequences are enforced in code rather than left to convention:

- **Read-only.** Every write in the adapter contract refuses locally
  (`SourceConflictError`) instead of issuing a request that should never be
  made. `list_installation_requests()` returns `[]` because the register
  genuinely does not exist — not because a call failed.
- **Live only.** Cameras advertise `live` and never `playback`, *and* the video
  adapter refuses a playback request outright. Two independent gates, because
  "there is no recording" should not depend on either one alone.

---

## The integrator's guide, mapped to code

| Rule from §3 of the guide | Where it lives | Asserted by |
|---|---|---|
| **DO** force RTSP over TCP | `edge-worker/app/grid.py` `_force_tcp_transport` - set before the first capture, because it is a process-wide FFmpeg option read at construction | `test_rtsp_transport_is_pinned_to_tcp` |
| **DON'T** trust the reported frame rate | Nothing reads `CAP_PROP_FPS`. `ReconnectingCapture.measured_fps` is computed from PTS; the catalogue's `fps` is stored as provenance only | `test_measured_fps_is_derived_from_pts_not_declared`, `test_no_code_path_reads_cap_prop_fps` |
| **DO** drive timing from PTS | `Frame.pts_ms` from `CAP_PROP_POS_MSEC`, never arrival time | `test_frame_timing_comes_from_pts_deltas_not_a_fixed_cadence` |
| **DON'T** assume a constant frame rate | `Frame.dt_ms` is the real elapsed PTS gap; a gap does not tear down the capture | `test_a_gap_is_not_treated_as_a_disconnect` |
| **DO** reconnect with backoff | `ReconnectingCapture`, 2 s → 30 s cap, never a tight loop | `test_a_real_drop_reconnects_with_backoff` |
| **DON'T** treat join-time decode warnings as fatal | `GRACE_FAILURES = 25` bad reads tolerated after a (re)connect | `test_join_time_decode_failures_are_tolerated` |
| **DON'T** assume a uniform grid | Per-camera codec/resolution/fps from `/api/ingest`; the grid really is mixed (H.264 + H.265, 720p→1440p) | verified against the live catalogue |
| **DO** expect a scene discontinuity | A backwards PTS jump sets `Frame.discontinuity` and withholds `dt_ms`, so a tracker is never fed an impossible delta | `test_backwards_pts_raises_discontinuity_and_resets_timing` |
| **DON'T** plan around obtaining copies | Frames are decoded from a live capture; nothing is written to disk | `test_nothing_is_written_to_disk` |
| **DON'T** publish to the gateway | No write verb appears in a call anywhere in the module | `test_module_only_ever_reads` |
| **DO** pace your load | Catalogue cached 30 s; one capture per camera, released on exit; dashboard tiles hold a session only while on screen | `test_capture_is_released_on_exit` |
| **Credentials required** (access model, 2026-09-10) | RTSP and WHEP authenticate every connection with the registered email and access password in the URL, the email's `@` percent-encoded. `with_credentials` injects them from `SENTINEL_GRID_EMAIL`/`SENTINEL_GRID_PASSWORD`; `safe_url` redacts them from every label, log line and report | `tests/test_grid_urls.py` |
| **Start from the catalogue** | `cameras.json` is read first; when the CDN gateway is unreachable (it has been down or 403 for hours while the RTSP gateway stayed up) `catalogue_or_fallback` composes the documented ids cam01..cam30 with the documented URLs so the worker keeps processing | `test_fallback_catalogue_when_gateway_unreachable` |

Every row above is pinned by a test in `tests/test_grid_capture.py` that runs
against a fake capture, so the rules hold whether or not the sandbox is
reachable. That matters more than it sounds: the grid's media plane fails
independently of its catalogue, and a rule only checked when the feed is up is
not really checked at all.

The catalogue is the contract, the URL pattern is not: `/api/ingest` is re-read
on a short TTL rather than hard-coded, because camera ids and the set of
cameras change.

---

## Transport

| Protocol | Port | Used for |
|---|---|---|
| RTSP | 8554 | edge inference (preferred); `rtsp://<email%40>:<password>@103.250.160.189:8554/stream/<id>` |
| WebRTC/WHEP | 8889 | recorded per camera (`GridCamera.whep_url`) for a low-latency browser preview; not wired into the dashboard, which stays on HLS |
| HLS | 443 | **browser preview — the only path that works everywhere** |

Ports 8554 and 8889 are blocked on many networks (they are unreachable from
this development machine; only 443 answers). The guide sanctions HLS explicitly
for that case, so the browser path is always HLS and the edge worker prefers
RTSP with an HLS fallback.

The gateway also 302s to an `http://` URL unless `?cookieCheck=1` is present,
which would downgrade the scheme mid-playlist. Every request carries it.

---

## How the video reaches a browser

The browser never learns that `live.corp8.cloud` exists.

```
browser → /api/v1/streams/{session_id}          (Vigentra, authorised, audited)
        → /api/v1/streams/{session_id}?p=<ref>  (each segment re-enters the same route)
                ↓ server-side only
          https://live.corp8.cloud/live/stream/<n>/...
```

HLS is a playlist of relative references, so a plain proxy would leak the
upstream host the moment the player resolved the first child playlist. The
broker rewrites **every** URI — bare lines and `URI="..."` attributes on
`EXT-X-MAP`, `EXT-X-PART`, `EXT-X-PRELOAD-HINT`, `EXT-X-KEY` and friends — to a
relative `?p=<encoded ref>`.

Relative, deliberately: `?p=…` resolves against whatever URL the playlist was
fetched from, so the rewrite stays correct behind the dashboard's
`/api/vigentra` proxy, behind any other reverse proxy, and when the API is
called directly — without this service needing to know its own public prefix.

Because every segment re-enters `/api/v1/streams/{id}`, **authorisation is
re-checked on every segment**, not just at session open. Revoking a grant stops
the feed within one segment.

`p` arrives from the browser and is treated as hostile. It must stay relative
and stay on the manifest's own host; a scheme, an authority, a leading `/` or a
`..` is refused rather than normalised, because a permissive resolver here
would turn the stream proxy into an open forward proxy.

---

## Where the metadata comes from

The grid publishes **no coordinates, no view direction and no camera class**.
`/api/ingest`, `/api/cameras` and the portal page carry only id, name, location
text, codec/resolution and the three stream URLs.

Those fields come from `reference/grid_cameras.json`, compiled by surveying the
feeds themselves. Three evidence sources:

1. **The catalogue** — authoritative for URLs, codec, resolution, fps.
2. **The burned-in overlay** on each stream — authoritative for the operator's
   own asset id and camera class, e.g. `Chiman bhai Bridge CSITMS-32_PTZ2`
   (a PTZ), `O.N.G.C. Office BS-103_B1` (a bullet), `CN VIDHYALAYA P2 RLVD`
   (red-light violation detection, so ANPR-capable). Several overlays state the
   view outright: `New By PassNr 66KV FIX-2 (From Vadla Fatak)`.
3. **OpenStreetMap** — approximate coordinates for landmarks identified in the
   frames.

Every record carries `geo_confidence`, surfaced on the camera page through
`coverage_description`, so a map pin never implies more precision than it has:

| Confidence | Count | Meaning |
|---|---|---|
| `verified` | 1 | operator survey |
| `geocoded` | 12 | named OSM feature match |
| `landmark_derived` | 7 | anchored to a nearby verified feature |
| `estimated` | 8 | locality right, junction not established |
| `district_centroid` | 2 | stream never reached |

Missing or malformed reference data is not fatal — the cameras still federate,
just without coordinates. A registry missing map pins beats a registry that
will not load.

### Two known discrepancies in the upstream data

- **Camera 6** is labelled `06 Timbavadi-Junagadh` in the catalogue, but its
  burned-in overlay reads `Madhuram Bypass Road Fix-2 (From Aksharvadi)` and
  OSM places `Madhuram Hospital` in **Bhavnagar** — roughly 250 km from
  Junagadh. Two independent sources beat the catalogue, so the adapter records
  Bhavnagar and the surveyed site name wins over the catalogue string.
- **Cameras 17, 18 and 22** report `live: true` in the catalogue while their
  playlists return HTTP 500 or time out. The health detail block says the
  status is *the grid's own claim, not a probe*, so an operator is not misled.

Both are worth reporting upstream per §5 of the guide.

---

## Running it

Enabled by default. To turn it off:

```bash
SENTINEL_GRID_ENABLED=false
```

### Running against real cameras only

The two demo departments carry synthetic cameras. Turn them off and the
registry federates nothing invented:

```bash
TRAFFIC_VMS_ENABLED=false
MUNICIPAL_VMS_ENABLED=false
```

Cameras belonging to a de-configured source are **deleted on the next sync**
(`_retire_unconfigured_sources`), so switching a department off does not leave
its rows behind pretending to be real. Switching it back on and syncing
recreates them.

What this costs: the grid is a single, live-only department, so with the mocks
off there is no second unit to demonstrate the cross-unit conversation with —
request, grant, revoke. That flow is the core of the access model, so keep the
mocks on when demonstrating it.

### Seeing every camera at once

**Live wall** in the sidebar (`/live`) shows every camera the account may watch,
with coordinates, view direction, resolution and codec on each tile. A reason is
required before anything opens, exactly as on the single-camera player.

Each tile opens its own session only while it is on screen and gives it back
when it scrolls away. That is the guide's "open only the cameras you are
actively processing" applied literally — thirty permanent sessions for a wall
someone is scrolling past would be exactly the load abuse it warns about, and
each one is an audited access besides.

Sign in as `grid.operator` / `Grid@2026`. The role holds `video:live` and
deliberately **not** `video:playback`.

Regenerate the reference data after re-surveying:

```bash
python scripts/compile_grid_reference.py
```
