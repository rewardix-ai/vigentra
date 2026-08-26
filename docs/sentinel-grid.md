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

| Rule from §3 of the guide | Where it lives |
|---|---|
| **DO** force RTSP over TCP | `edge-worker`: `OPENCV_FFMPEG_CAPTURE_OPTIONS=rtsp_transport;tcp`, set before the first capture because it is a process-wide FFmpeg option |
| **DON'T** trust the reported frame rate | Nothing derives timing from `CAP_PROP_FPS`; the catalogue's `fps` is stored as metadata only |
| **DO** drive timing from PTS | Frame timing reads `CAP_PROP_POS_MSEC`, never arrival time |
| **DON'T** assume a constant frame rate | Inter-frame gaps are tolerated, not treated as a disconnect |
| **DO** reconnect with backoff | 2 s → 30 s cap, never a tight loop |
| **DON'T** treat join-time decode warnings as fatal | A run of failed reads is tolerated before a drop is declared; the grid is mixed H.264/H.265 |
| **DON'T** assume a uniform grid | Per-camera codec, resolution and fps come from `/api/ingest`; the grid really is mixed (H.264, H.265, 720p→1440p) |
| **DO** expect a scene discontinuity | A backwards PTS jump resets long-lived track state |
| **DON'T** plan around obtaining copies | Nothing fetches `/stream/<id>`; that path is the browser fallback and yields a partial file that looks complete |
| **DON'T** publish to the gateway | The adapter has no write path at all |
| **DO** pace your load | Catalogue reads are cached (30 s TTL); sessions are closed when finished |

The catalogue is the contract, the URL pattern is not: `/api/ingest` is re-read
on a short TTL rather than hard-coded, because camera ids and the set of
cameras change.

---

## Transport

| Protocol | Port | Used for |
|---|---|---|
| RTSP | 8554 | edge inference (preferred) |
| WebRTC/WHEP | 8889 | not used |
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
browser → /api/v1/streams/{session_id}          (Sentinel, authorised, audited)
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
`/api/sentinel` proxy, behind any other reverse proxy, and when the API is
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
