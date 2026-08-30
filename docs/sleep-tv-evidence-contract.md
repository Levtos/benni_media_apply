# Sleep-TV deadline and evidence contract

- Status: implemented for technical testing; not released, installed, or live verified
- Date: 2026-08-30
- Tracking: [Levtos/benni-core-state#59](https://github.com/Levtos/benni-core-state/issues/59)
- Integration version: `0.19.6`
- Evidence contract: `1.0.0`

Media Apply is the sole owner of the physical TV action and its timing. Entering
`provisional_sleep` with the canonical TV Master active creates an absolute
`now + 45 min` deadline. A physical extension adds 45 minutes to the current
deadline. Manual PS→S resets the deadline to a fresh `now + 45 min`; switching
the TV on during PS/S does the same without waking Bio.

Only the canonical TV Master can certify TV state. `unknown`,
`unavailable`, a WebOS/network disconnect, or a failed turn-off command never
counts as off. Verified off starts a ten-minute continuous confirmation window;
active or unavailable input breaks that window. The public
`sensor.benni_media_apply_sleep_tv_evidence` reports:

- state `inactive | tv_active | confirming_off | off_confirmed | unavailable`
- `sleep_reference_start`, absolute `deadline`, and `timer_source`
- `off_confirmed_since`, `off_confirmed_at`, remaining confirmation seconds
- `tv_state_quality`, command/warning markers, and contract version

Deadline, confirmation timestamps, reference, and exactly-once markers are
stored in Home Assistant storage. Startup reloads the record before the first
coordinator refresh, reconciles current canonical TV truth, schedules remaining
wall-clock time, and does not blindly repeat a marked deadline action. Leaving
PS/S cancels both deadline and confirmation.

The existing warning remains apply-gated. The timer is observed in Shadow, but
the TV service call occurs only when `apply_enabled` is true. Media Apply does
not decide or write Bio-State; Core State consumes the evidence entity.
