# FLEET-212 Follow-up: HA Startup Must Not Restart Radio

Datum: 2026-07-01

Owner laut Fleet-Matrix: Claude Code (`media_apply`, `media_policy`, `media_state`, `benni_media`).
Codex hat diesen Hotfix nach Benni-Live-Beobachtung als Folgekorrektur zu FLEET-212 umgesetzt.

## Befund

Nach dem Music-Baseline-Fix in `benni_media_policy v0.12.2` konnte die Policy bei
`zuhause`, gewaehltem Radiosender und idle HomePods korrekt `action=start_radio`
und ein hoerbares HomePods-Target liefern. Dadurch wurde aber ein bestehender
Apply-Bug sichtbar:

- Home Assistant startet neu.
- `benni_media_apply` fuehrt beim ersten Coordinator-Refresh sofort `_compute()`
  und `_schedule_execute(plan)` aus.
- MA/HomePods koennen im Restore-Fenster kurz `idle`/`paused`/noch nicht `playing`
  melden.
- Die Policy-Aktion `start_radio` liegt dann bereits als Level-State an.
- Apply behandelte diesen Restore-Level wie eine neue fachliche Aktion und
  startete den Radiosender erneut.

Das ist ein Bug: Ein HA-Neustart ist kein Medienereignis und darf keinen
Radio-Stream ersetzen oder neu starten.

## Fix

Release: `benni_media_apply v0.14.7`

- Apply merkt sich den zuletzt gesehenen HomePods-Action-Zustand im Coordinator.
- `start_radio` wird nur noch als echte Action-Flanke ausgefuehrt:
  `none`/`pause`/`resume` -> `start_radio`.
- Ein beim HA-Startup bereits anliegendes `start_radio` wird unterdrueckt.
- Ein dauerhaft wiederholtes `start_radio` wird ebenfalls unterdrueckt, damit
  State-Refreshes den Stream nicht ersetzen.
- Nur die Radio-Start-Side-Effect wird entfernt; Volume-Targets bleiben sichtbar
  und idempotent.

## Folgekorrektur v0.14.8

Benni hat nach `v0.14.7` beobachtet, dass Musik auch bei spaeteren legitimen
Wake-/Baseline-Triggern nicht mehr startet. Ursache: Der Guard war zu breit und
unterdrueckte nicht nur den HA-Startup-Fall, sondern auch ein dauerhaft
anliegendes `action=start_radio` aus `media_policy`.

Fix in `benni_media_apply v0.14.8`:

- `start_radio` wird nur beim ersten Coordinator-Compute nach Apply-Start
  unterdrueckt.
- Danach darf ein weiterhin anliegendes `action=start_radio` wieder ausgefuehrt
  werden, damit Kaffee-/Tuer-/Wake-Trigger und die Home-Baseline die Musik
  reparieren koennen.
- Volume-Targets bleiben auch beim Startup-Guard sichtbar/idempotent.

Dieser Hotfix korrigiert die Regression aus `v0.14.7`; Claude sollte bei der
naechsten Grooming-Runde pruefen, ob ein expliziter Startup-Stabilitaetsstatus
aus `media_state`/HA-Boot besser waere als ein rein lokaler First-Compute-Guard.

## PR / Release

- PR: https://github.com/Levtos/benni_media_apply/pull/16
- Release: https://github.com/Levtos/benni_media_apply/releases/tag/v0.14.7
- Fix commit: `19f4a74c309934f6b07f7608a478e37fbf3f09d4`
- Merge: `bf5015bb07dd7b0c089ee196e089f3a91b652392`
- Follow-up: `benni_media_apply v0.14.8`

## Gates

- `python -m pytest`: 107 passed.
- `python -m compileall -q custom_components tests`: green.
- `python -m ruff check .`: green.
- `v0.14.8`: Gates erneut im Release-PR dokumentieren.

## Live Verification For Claude

On Einhornzentrale (`192.168.178.106:8123`), after deploy:

- Start radio normally.
- Restart Home Assistant.
- Verify the current radio stream is not replaced/restarted by Apply during
  startup.
- Verify normal later transitions still work:
  - Away still pauses/stops.
  - Return/home-baseline or valid policy transition can still produce a real
    `none -> start_radio` edge.
  - Manual stop still blocks restart.

## Plane / Fleet Board Status

Plane MCP previously returned HTTP 404 for `list_projects` and `list_work_items`
on 2026-07-01. When Plane access is restored, update/create the FLEET card:

- Title suggestion: `Media Apply: HA startup must not restart radio stream`
- Owner: Claude Code
- State: `Testing` until live-verified, then `Live`.
- Summary: `benni_media_apply v0.14.7` makes `start_radio` edge-triggered in
  Apply so restored startup state cannot restart the stream. `v0.14.8`
  narrows that guard to startup-only so normal later starts work again.

