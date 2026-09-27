# Sample gap-analysis report

Generated 27 Sep 2026 16:37 UTC from the running Vigentra registry (`scripts/export_samples.py`); the dashboard's
*Reports → Gap analysis* page shows the same figures live, scoped to the viewer's departments.

50 cameras in service across 10 districts, 20 decommissioned and kept
on the register for the audit trail. A district is **thin** when fewer than 3 cameras in it are in
service; a camera is **ageing** at 5 years from installation.

## Coverage by district

| district | cameras | online | degraded | offline | unavailable | thin | departments |
|---|---|---|---|---|---|---|---|
| London | 19 | 19 |  |  |  |  | Traffic Police |
| Ahmedabad | 10 | 10 |  |  |  |  | Municipal Corporation, Traffic Police |
| Gujarat | 7 | 7 |  |  |  |  | Traffic Police |
| Navsari | 4 | 4 |  |  |  |  | Municipal Corporation |
| Junagadh | 4 | 4 |  |  |  |  | Traffic Police |
| Rajkot | 2 | 2 |  |  |  | yes | Municipal Corporation |
| Bhavnagar | 1 | 1 |  |  |  | yes | Traffic Police |
| Gandhinagar | 1 | 1 |  |  |  | yes | Traffic Police |
| Gir Somnath | 1 | 1 |  |  |  | yes | Traffic Police |
| Kutch | 1 | 1 |  |  |  | yes | Traffic Police |

## Cameras not online now

None: every camera in service is online or degraded.

## Ageing infrastructure (5 years or more)

None of the cameras in service is recorded as installed 5 or more years ago.

## Reading it

- Thin districts are where coverage planning starts: a single camera is a blind spot the day it fails.
- Cameras offline now are listed with their last heartbeat, so a maintenance agency can be sent to one.
- Installation dates come from each department's installation register; grid cameras have none, so they
  cannot age out of this report and are listed by their catalogue entry instead.
