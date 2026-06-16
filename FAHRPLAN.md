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
| Radio-Katalog (Sender-Map + MA play_media) | `media_scripts` | **portiert Ph4b** (inline RADIO_CATALOG + Script-Fallback) |
| TV-WoL (R12) | `media_automations` #1 | **portiert Ph4c** (turn_on + optionale MAC; webOS-Leuchtfeuer-Automation bleibt 24/7) |
| R20-Restore (Quiet-Ende → Pre-Quiet + Ramp-Up) | — | **NEU, spätere Phase** |
| R13/R14 Denon-Nachlauf 90s | nicht gefunden | **NEU, spätere Phase** |
| R24/R25 Sleep-TV-Off 45min + verlängern | nicht gefunden | **NEU, spätere Phase** |
| R1/R3 Idempotenz + FIFO-Queue | tlw. (`mode: restart`) | Idempotenz Ph1; **Debounce/Serialize Ph4a** |
| OQ-2 ATV-Pre-Snapshot persistieren | RAM (Toolbox) | **NEU, spätere Phase** |

## Phasen
- **Phase 1 — Scaffold + Kern-Apply (diese Karte zuerst).** Pure-Logic
  (Ramp-Sequenz, Apply-Plan, Idempotenz) + Coordinator (Entity-State-Plumbing,
  Ramp-Task, Service-Calls) + Apply-Gate. Volume (HomePods geramped 16×1s,
  Tiny-Delta 0.02 → direkt; Denon hart), HomePods-Action (pause/play; start_radio
  delegiert), Subwoofer on/off. Quiet → direkt (kein Ramp). **Shadow-safe**
  (`apply_enabled` default OFF): Plan wird als Debug-Sensor exponiert, NICHT
  ausgeführt, bis freigegeben.
- **Phase 2 — Restore (R20) ✅ (0.2.0):** Pre-Quiet-Snapshot (ApplyState) +
  Ramp-Up auf Pre-Quiet bei Quiet-Ende (HomePods rampen, Denon hart); Quiet-
  Eintritt bricht den laufenden Ramp ab → sofort 0.10 (Phase-1-quiet_override +
  Coordinator-Ramp-Cancel). 29 pure-logic-Tests grün.
- **Phase 3a — Denon-Nachlauf R13/R14 ✅ (0.3.0):** Pure-Logic
  `decide_denon_nachlauf` (arm/cancel/pause-Flanken + Armed-Buchwerk) +
  Coordinator-Countdown (abbrechbarer asyncio-Task pro Timer; Sleep pausiert
  den TV-Timer, Resume = Neustart). Expiry → `media_player.turn_off` Denon,
  gegatet durch `apply_enabled` (Shadow). Inputs `pc_power_on`/`tv_power_on`
  als **DEFERRED Bindings** (PROFILE_PREFILL leer) bis FLEET-54 die core_devices-
  Atomic-Slugs festklopft → bis dahin None ⇒ kein Arm (doppelt safe). `denon_power`
  leitet sich notfalls aus dem Denon-media_player ab, `bio_state` aus core_state.
  Observability: `binary_sensor …_denon_nachlauf_active`. 21 neue pure-logic-Tests.
- **Phase 3b — Sleep-TV-Off R24/R25:** zurückgestellt — braucht TV-Warnhinweis +
  Lichtschalter-Verlängern (grenzt an notification_router/door) + Sleep-Lautstärken.
  Separate Karte, sobald Notify/Tasten-Scope geklärt.
- **Phase 4a — Debounce (R2) + serialisierte Ausführung (R3) ✅ (0.7.0):** Geräte-
  Schaltung läuft jetzt über ein konfigurierbares Debounce-Fenster (`debounce_seconds`,
  default 5s) → Trigger-Bursts fallen zu EINER Aktion zusammen; **Quiet bricht
  sofort durch** (`EXEC_IMMEDIATE`, Ramp-Abbruch), Shadow führt gar nicht aus.
  Ausführung serialisiert über `asyncio.Lock` (latest-wins statt Race, R3). Triviale
  Re-Evals (kein Soll≠Ist) stoßen das Fenster nicht neu an (`ApplyPlan.has_work`).
  Pure-Logic: `logic.execution_mode(plan)` (shadow/immediate/debounce). Cockpit
  (FLEET-70): `status().debounce = {window_s, pending, remaining_s, plan}` (Restzeit
  + der eine konsolidierte Pending-Plan, latest-wins statt Stale-FIFO) +
  `settings.debounce_seconds` (v0.7.1). 6 neue pure-logic-Tests (51 grün gesamt).
  Timing bleibt Coordinator (HA), nur die Klassifikation ist HA-frei getestet.
  **Offen für FLEET-70:** Frontend rendert `remaining_s`/`plan` noch nicht
  (Umbrella `benni_media` Apply-Tab) — WS-Contract liefert es bereits.
- **Phase 4b — Radio-Katalog-Port ✅ (0.8.0):** Sender→URI-Katalog (`RADIO_CATALOG`,
  6 Sender, KOPIE aus `script.media_radio_start`) inline portiert. `start_radio`
  ruft jetzt direkt `music_assistant.play_media` (media_type=radio, enqueue=replace)
  + verzögertes `media_play` (`radio_play_delay_seconds`, 2s); Sender aus gebundenem
  `input_select.media_radio_station`. Gates wie im Script: `media_radio_ready` an +
  `media_manual_playback_active` aus (beide None=ungebunden ⇒ non-regressiv erlaubt).
  **Fallback:** Sender ungebunden/unbekannt → weiterhin Script-Delegation. Pure-Logic
  `logic.resolve_radio_uri()` + start_radio-Gates, 7 neue Tests (58 grün). YAML-Script
  bleibt vorerst (Stop/Clear-Latch + Fallback); Löschen erst beim FLEET-36-Cut-over.
- **Radio-Shortcuts + MA-Suche ✅ (0.9.0):** manuelle Sender-Steuerung fürs Cockpit.
  `async_play_radio(media_id)` spielt einen Sender SOFORT (MA `play_media` radio/replace
  + verzögertes `media_play`) — **Shadow-Bypass**: bewusster User-Befehl, unabhängig von
  `apply_enabled` (nur der automatische Policy-Apply bleibt gegatet). `async_search_radio(query)`
  sucht via `music_assistant.search` (media_type=radio, return_response) → normalisierte
  Treffer `{name,uri,image,favorite}` (mehrere Provider: radiobrowser/library/ard). Defaults
  als Shortcut-Liste in `status().radio.defaults` (`logic.radio_defaults()`, getestet).
  Bedient wird beides über das Umbrella-Write-Gateway (`apply/play_radio|search_radio`).
- **Phase 4c — TV-WoL (R12) ✅ (0.10.0):** Wechsel auf ein Bildschirm-Szenario
  (`media_device` ∈ {tv, appletv}) bei ausgeschaltetem TV → TV einschalten, **sofort
  (kein Debounce)**, edge-getriggert (feuert 1× pro Episode, Reset bei TV an / kein
  Bildschirm). TV-Power R11: WebOS-State (off/standby) primär, Wattage-Fallback.
  Aktion: `media_player.turn_on` (löst das webOS-„Leuchtfeuer" aus — die WoL-Automation
  bleibt 24/7, die LG-Integration braucht sie für den On/Off-Status) **+ optionale
  variable MAC** (`tv_wol_mac`) → eigenes `wake_on_lan.send_magic_packet`. **Apply-gated**
  (automatische Aktion, Shadow bis Scharfschalten). Pure-Logic `decide_tv_wol`/`_tv_is_off`,
  9 neue Tests (68 grün). Observability: `status().tv_wol`.
- **Phase 4d — OQ-2 (ATV-Pre-Snapshot persistieren).** offen (gehört evtl. zu media_state,
  Rollback-Szenario R7 — zuerst klären, wo der Snapshot lebt).

## Konstanten (§6, alle konfigurierbar)
16 Ramp-Schritte · 1s Schrittdelay · 0.02 Tiny-Delta · 0.10 Ducked · 90s/90s
Denon-Nachlauf · 45min Sleep-TV-Off.

## Verifikation
Lokal kein HA/dulwich → `py_compile` + pure-logic-Tests (Ramp-Sequenzen,
Idempotenz, Plan-Gating). Apply bleibt shadow-gated bis Live-Verify auf
`einhornzentrale` (Canary). Erst nach Verify ersetzt media_apply die YAML-Apply-
Automationen + Toolbox-Quelle wird gelöscht (FLEET-36 Strangler-Abschluss).
