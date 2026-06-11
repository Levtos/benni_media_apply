# FAHRPLAN — benni_media_apply

L?-Apply / **Ausführungsschicht** (Executor). Konsumiert `benni_media_state`
(Szenario) + `benni_media_policy` (Targets/Action/Gates) **nur über HA-Entity-State**
und schaltet die echten Geräte. Spiegelt das Muster light_policy → scene_presets:
Policy denkt, Apply tut. OQ-1 entschieden: eigenständiges Modul (kein YAML).

Lastenheft: `einhornzentrale/docs/lastenhefte/reviewed/media/` (v3.1, §5.x R1–R25).

## Abgrenzung (wer macht was)
- **media_state** (L1): Szenario/Context, quiet_mode, entertainment.
- **media_policy** (L2): audio_owner, action, volume_policy, volume_target_*,
  homepods_should_pause/resume_allowed, subwoofer_allowed, volume_apply_allowed.
- **media_apply** (hier): rechnet NICHTS neu — nimmt die Targets/Action und führt
  sie idempotent + geramped aus. Apply-Gate: `apply_enabled` (eigene Option,
  Shadow-safe default OFF) × `volume_apply_allowed` (pro Entscheidung aus policy).
- **scene_presets**: besitzt das **Bias-Light** (Look schaltet `living_bias_light_plug`)
  — NICHT media_apply.

## Apply-Domänen (Heutiges YAML → media_apply)
Analyse-Stand 2026-06-11 (alter Layer: `einhornzentrale/packages/media/`):

| Domäne | Heute (YAML) | media_apply |
|---|---|---|
| HomePods-Action (pause/play/start_radio) | `media_automations` #4 | **Phase 1** (start_radio → delegiert an `script.media_radio_start`) |
| Volume | direktes `volume_set` (KEIN Ramp!) | **Phase 1 + NEU: Ramps** |
| Subwoofer-Plug | on/off | **Phase 1** |
| Apply-Gate | `system_apply_ready` × `volume_apply_allowed` | **Phase 1**: eigenes `apply_enabled` × `volume_apply_allowed` |
| Stop-Latch | `input_boolean.media_stop_latch` (shared) | konsumiert (media_policy liest ihn auch) |
| Radio-Katalog (Sender-Map + MA play_media) | `media_scripts` | **delegiert Ph1**, Port später |
| TV-WoL | `media_automations` #1 | später (bleibt vorerst YAML) |
| R20-Restore (Quiet-Ende → Pre-Quiet + Ramp-Up) | — | **NEU, spätere Phase** |
| R13/R14 Denon-Nachlauf 90s | nicht gefunden | **NEU, spätere Phase** |
| R24/R25 Sleep-TV-Off 45min + verlängern | nicht gefunden | **NEU, spätere Phase** |
| R1/R3 Idempotenz + FIFO-Queue | tlw. (`mode: restart`) | Idempotenz Ph1; Queue spätere Phase |
| OQ-2 ATV-Pre-Snapshot persistieren | RAM (Toolbox) | **NEU, spätere Phase** |

## Phasen
- **Phase 1 — Scaffold + Kern-Apply (diese Karte zuerst).** Pure-Logic
  (Ramp-Sequenz, Apply-Plan, Idempotenz) + Coordinator (Entity-State-Plumbing,
  Ramp-Task, Service-Calls) + Apply-Gate. Volume (HomePods geramped 16×1s,
  Tiny-Delta 0.02 → direkt; Denon hart), HomePods-Action (pause/play; start_radio
  delegiert), Subwoofer on/off. Quiet → direkt (kein Ramp). **Shadow-safe**
  (`apply_enabled` default OFF): Plan wird als Debug-Sensor exponiert, NICHT
  ausgeführt, bis freigegeben.
- **Phase 2 — Restore (R20):** Pre-Quiet-Snapshot + Ramp-Up bei Quiet-Ende;
  laufenden Ramp bei Quiet-Eintritt abbrechen → sofort 0.10.
- **Phase 3 — Timer-Regeln:** Denon-Nachlauf R13/R14 (90s, abbrechbar,
  Sleep pausiert TV-Timer); Sleep-TV-Off R24/R25 (45min-Warnung + Verlängern).
- **Phase 4 — Radio-Katalog-Port + TV-WoL + FIFO-Queue (R1/R3) + OQ-2.**

## Konstanten (§6, alle konfigurierbar)
16 Ramp-Schritte · 1s Schrittdelay · 0.02 Tiny-Delta · 0.10 Ducked · 90s/90s
Denon-Nachlauf · 45min Sleep-TV-Off.

## Verifikation
Lokal kein HA/dulwich → `py_compile` + pure-logic-Tests (Ramp-Sequenzen,
Idempotenz, Plan-Gating). Apply bleibt shadow-gated bis Live-Verify auf
`einhornzentrale` (Canary). Erst nach Verify ersetzt media_apply die YAML-Apply-
Automationen + Toolbox-Quelle wird gelöscht (FLEET-36 Strangler-Abschluss).
