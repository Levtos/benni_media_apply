# benni_media_apply

**Ausführungsschicht / Executor** der Benni-Media-Kette. Konsumiert
`benni_media_state` (Szenario) + `benni_media_policy` (Targets/Action/Gates)
**nur über HA-Entity-State** und führt sie an den echten Geräten aus — idempotent
(nur bei Ist≠Soll). HomePods geramped (16×1s, Tiny-Delta direkt); der Denon
kann keine Rampe — harter Set, und seit benni_media#13 am R2-Fenster vorbei.
Volume-Befehle gehen nur an eine spielende bzw. gerade gestartete HomePod-Gruppe,
nie an eine pausierte/idle (benni_media#16 — `volume_set` weckt den AirPlay-Player
und ist dort unhörbar; Pause ist der Stop-Mechanismus, nicht `volume 0`).

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

Radio-Autostart/-Resume startet nie Musik, solange ein Bildschirm-Stack (TV)
das Audio besitzt (benni_media#16): `should_autostart_radio` prüft zusätzlich
`_tv_is_off`, ein wartender verzögerter Resume wird bei TV-Übernahme abgebrochen,
und unmittelbar vor dem echten Play-Kommando wird der stabile Kontext erneut
geprüft — robust gegen kurzes TV-Master-Flackern beim Hochfahren.

Siehe `FAHRPLAN.md`.

## Verifikation
Lokal kein HA → `py_compile` + pure-logic-Tests (`tests/test_logic.py`).
Apply-Verdrahtung nur live verifizierbar (Canary `einhornzentrale`, shadow-gated).
