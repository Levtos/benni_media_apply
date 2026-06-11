# benni_media_apply

**Ausführungsschicht / Executor** der Benni-Media-Kette. Konsumiert
`benni_media_state` (Szenario) + `benni_media_policy` (Targets/Action/Gates)
**nur über HA-Entity-State** und führt sie an den echten Geräten aus — idempotent
(nur bei Ist≠Soll) und geramped (HomePods 16×1s, Tiny-Delta direkt; Denon hart).

Muster: light_policy → scene_presets. Policy denkt, Apply tut.

## Apply-Gate (Shadow-safe)
`apply_enabled` (Option, default **OFF**) × `volume_apply_allowed` (pro
Entscheidung aus media_policy). Im Shadow wird der Apply-Plan berechnet und als
Status-Sensoren exponiert, aber **NICHT** ausgeführt. Erst einschalten, wenn der
Shadow stimmt.

## Phase 1 (FLEET-40)
HomePods-Action (pause/play; `start_radio` → delegiert an ein Script),
Volume mit Ramps, Subwoofer on/off. Restore (R20), Denon-Nachlauf (R13/R14),
Sleep-TV-Off (R24/R25), Radio-Katalog-Port, TV-WoL, FIFO-Queue folgen.

Siehe `FAHRPLAN.md`.

## Verifikation
Lokal kein HA → `py_compile` + pure-logic-Tests (`tests/test_logic.py`).
Apply-Verdrahtung nur live verifizierbar (Canary `einhornzentrale`, shadow-gated).
