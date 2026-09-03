# Vigentra — demo script

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
