# Vigentra — demo scripts

Two recordings are submitted (Step 5 of <https://sentinel.gujarat.gov.in/problems>), each at most three
minutes, screen-recorded from the running platform. The brief lists what each must *clearly
illustrate*; the beats below are in that order and nothing else goes in. The longer walkthrough of the
registry and the access model follows as an appendix: it is the material for questions, not for the
upload.

Before recording: `docker compose up --build`, every service healthy, the watchlist **empty of the
plate you are about to add**, the alerts page with nothing open. Record at 1920x1080 with the page
zoomed as in the 15 Sep recording. Say the four beat names aloud or caption them; a juror scoring
against the list should be able to tick each one without rewinding.

**Recording it.** `deliverables/tools/record_demo.py` drives a separate Chrome through exactly these
beats with a visible pointer and captions, and `edit_demo.py` cuts the result, shortening the wait for
your password and the wait for the alert. You sign in and type your password; the script never does.
The commands are at the top of `record_demo.py`.

## A. Own-feed demonstration (≤ 3:00)

Feed: the Delhi traffic clip served by the Traffic Police VMS. "Footage of your choice" is what the
brief asks for. Use **HR26CC2083** as the wanted plate: the engine confirms it on every run.

| Time | Beat the brief names | What is on screen |
|---|---|---|
| 0:00–0:35 | **Onboarding and processing of a feed** | `/installations/new`: the camera's form, submitted; `/registry`: it appears on the map and in the table with its department and status; open it and the feed plays |
| 0:35–1:15 | **AI-powered detection and analytics** | `/live`: the tile full screen with vehicle boxes, plate boxes and settled readings drawn live; `/detections` filtered to this camera, counts rising |
| 1:15–1:45 | **Correlation with a watchlist database** | `/watchlist`: add HR26CC2083, category *stolen*, a reason and a case reference; show the entry with who added it; say that the matcher runs at ingest, exact and one-glyph-near |
| 1:45–2:30 | **Automatic real-time alert and visualisation** | `/alerts` open beside the feed; the vehicle passes, the alert appears within seconds with camera, time, priority, category and exact/near; acknowledge it |
| 2:30–3:00 | (the test case, in one move) | From the alert open the plate's route on `/plates`: every sighting, timestamped, on the map; then `/audit`: the watchlist entry, the alert and the acknowledgement are all there |

## B. Government-feed demonstration (≤ 3:00), submitted with the output report

Feed: the Sentinel grid. Record **by the recording's daylight** — plates on these cameras are legible
from about 08:00 to 19:00 of the recording's clock and not at night (`docs/anpr-cam06-day.md`).

| Time | What the brief asks | What is on screen |
|---|---|---|
| 0:00–0:30 | **Onboard the Government feeds** | `/registry` filtered to the grid source: 30 of 30 cameras from the catalogue, online, on the Gujarat map; say "read from `/api/ingest`, not hard-coded" |
| 0:30–1:10 | **Live or recorded viewing** | `/live`: the wall with live detection on every tile; click CAM06, full screen, live ANPR |
| 1:10–1:50 | **Analytics output on the feed** | `/detections` and `/incidents` for grid cameras; a settled plate on CAM06 with its crop |
| 1:50–2:30 | **Watchlist and alert on the Government feed** | add a plate CAM06 is about to show to `/watchlist`; the alert arrives; acknowledge |
| 2:30–3:00 | **Output report with timestamps** | `/reports/anpr`: plates with camera and time; export; the same file is what is uploaded (`scripts/anpr_report.py`) |

Upload both as unlisted YouTube links or Drive links with "anyone with the link — viewer", and put the
links in `docs/submission.md`.

---

# Appendix — the registry and access-model walkthrough

A walkthrough of the federated registry and the video access model. Around
twelve minutes. Every input is synthetic.

The story to tell while clicking: **camera records federate automatically;
footage is asked for.** Steps 1–8 show the first half, steps 9–15 the second.

Assume every service is healthy: `docker compose up --build`, then open
<http://localhost:3000/login>. Demo accounts are on the sign-in page.

---

## 1. Sign in as a Traffic installation operator

Use `traffic.installer` / `Install@2026`. The header shows the environment
label, the health dot, the role and the department. The left rail exposes only
the sections this role is scoped to.

## 2. Create a new CCTV installation form

Click **New CCTV installation**. Five steps: identity, ownership and location,
technical metadata, local access policy, review. Point out the notice at the
top — this form goes to the Traffic Police's own CCTV/VMS system and is
validated there.

## 3. Save it as a draft

Fill sufficient fields (the defaults help). Click **Save as draft** on the
review step. The page navigates to the record — status `DRAFT`.

## 4. Show validation actually holding

Before submitting, go back and set the latitude to something outside Gujarat —
say `8.0`. Click **Submit & register**. The Traffic system returns
`VALIDATION_FAILED` with its own issue list, naming the jurisdiction check.

This is worth pausing on: with no approver in the loop, validation is the only
thing between a typo and the state registry, so it checks that a Gujarat
department is commissioning a camera in Gujarat — not merely somewhere on
Earth.

## 5. Correct it and submit

Restore a sensible coordinate and submit again. The record goes straight to
`REGISTERED` — there is no approval queue, and nobody had to be found to sign
anything. The unit had already decided to install the camera.

## 6. Synchronise metadata to Vigentra

Click **Synchronise metadata**, or return to the overview and use the button
at the top. The banner shows how many registered records synchronised and how
many unregistered ones were skipped (both mocks seed a draft, so the skip
count is never zero).

## 7. Show the camera in the central registry

Open **Camera registry**. The new camera sits alongside seed cameras from both
departments. Filter by department or district to show the registry is
genuinely federated.

## 8. Sign in as the *other* department and find it anyway

Sign out, sign in as `municipal.operator` / `Municipal@2026`, open the
registry. The Traffic camera you just created is there.

This is the change worth showing to anyone who saw an earlier build: a
Municipal officer can find a Traffic camera. Knowing that a camera is mounted
at a public junction is not the sensitive part — the footage is, and that is
gated separately.

Open its detail page. Note the visibility line: this reader gets `standard`
depth, and fields like the installation vendor come back marked *withheld for
your role*. The unit's internal operational data stays its own.

---

## 9. Try to watch it

On the same camera, the footage banner reads **Ask Traffic Police for
footage** rather than a flat refusal, with a **Request access** button. Show
the API saying the same thing:

```bash
curl -s -X POST http://localhost:8000/api/v1/video-sessions \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"camera_id":"<id>","mode":"live","reason":"demo"}' | jq .detail
```

```json
{ "code": "VIDEO_ACCESS_DENIED", "state": "needs_unit_approval",
  "owning_department": "Traffic Police",
  "request_access_at": "/api/v1/video-access-requests" }
```

The refusal names which kind of no it is. A flat `denied` would send this
officer to raise a support ticket.

## 10. Raise the request

Click **Request access**. A reason is mandatory — type a plausible one and a
case reference. Point out that this reason is shown to the owning unit *and*
written to the audit trail: it is the record of why the footage was looked at.

## 11. Decide it, as the owning unit

Sign in as `traffic.approver` / `Approve@2026` and open **Access requests**.
The request is in *Requests for your unit's cameras* with the reason and the
case ID. Click **Grant**.

Say what the grant is and is not: it names *that one officer*, not their
department; it covers this one camera; it expires on its own after seven days;
and it can be revoked at any moment.

## 12. Watch the footage

Sign back in as `municipal.operator`, open the camera, open the session. The
video plays, watermarked with the username and timestamp. The URL in the
address bar is `/api/v1/streams/{session_id}` — an opaque Vigentra address.
Open dev tools → Network: no RTSP address, no department hostname, no upstream
ticket anywhere in the response.

## 13. Revoke it and show the stream stop

Sign back in as `traffic.approver`, open **Access requests**, click **Revoke**.
Return to the Municipal session and scrub the video. The next range request is
re-authorised and refused — revocation stops a stream already playing, not
just the opening of new ones.

## 14. Suspend a camera and show it become unavailable

As a `video_access_approver`, open a synchronised camera's installation record
and **Suspend** it with a reason. Return to the overview, click **Synchronise
metadata**, open the registry: the camera is still listed, now `SUSPENDED` and
`unavailable`. A withdrawn camera is never silently dropped from the register.

## 15. Open the audit log

Sign in as `auditor` / `Auditor@2026`. Filter by action. The whole story is
there: sign-ins, form creation, submission, synchronisation, the refused
viewing attempt from step 9, `video_access_requested`, `video_access_granted`,
`video_session_opened`, `video_stream_accessed`, `video_access_revoked`.

Denials are recorded as deliberately as successes — which is the point of the
trail.

---

## Failure-isolation encore

Time permitting, stop one department:

```bash
docker compose stop municipal-vms
```

Refresh the overview. Traffic Police cameras stay healthy; Municipal cameras
turn `offline` with an explanatory reason on the department-systems row, and
the installation register degrades to its last mirrored state rather than
failing outright. Bring it back:

```bash
docker compose start municipal-vms
```

Click **Synchronise metadata**. Municipal cameras recover. The outage window
is visible in the audit log.
