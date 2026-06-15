"""Konstanten von benni_media_apply (Ausführungsschicht / Executor).

Eigenständige HA-Integration. Konsumiert benni_media_state (Szenario) +
benni_media_policy (Targets/Action/Gates) AUSSCHLIESSLICH über HA-Entity-State —
kein Cross-Modul-Python-Import (Contracts werden KOPIERT, nicht importiert).

Phase 1 (FLEET-40): Kern-Apply — HomePods-Action (pause/play; start_radio
delegiert an script.media_radio_start), Volume (HomePods geramped, Denon hart),
Subwoofer on/off. Apply-Gate: apply_enabled (Option, Shadow-safe OFF) ×
volume_apply_allowed (pro Entscheidung, aus media_policy).

Lastenheft: einhornzentrale/docs/lastenhefte/reviewed/media/ (v3.1)
"""
from __future__ import annotations

from typing import Any, Final

DOMAIN: Final[str] = "benni_media_apply"
MODULE_ID: Final[str] = "media_apply"
NAME: Final[str] = "Benni Media Apply"

DATA_COORDINATOR: Final[str] = "coordinator"

# Panel / WebSocket-API. Der WS-Contract ist das Bleibende (Umbrella-fähig),
# das Panel ist Wegwerf (folgt). Read frei, Schreiben Admin.
WS_GET_STATUS: Final[str] = f"{DOMAIN}/get_status"
WS_SET_APPLY_ENABLED: Final[str] = f"{DOMAIN}/set_apply_enabled"

# Panel (Wegwerf-Frontend auf dem WS-Contract).
DATA_VIEW_PANEL: Final[str] = "_view_panel"
DATA_VIEW_STATIC: Final[str] = "_view_static"
PANEL_URL_PATH: Final[str] = "benni_media_apply"
PANEL_TITLE: Final[str] = "Media Apply"
PANEL_ICON: Final[str] = "mdi:cast-audio-variant"
PANEL_ELEMENT: Final[str] = "bma-app"
FRONTEND_DIR_URL: Final[str] = "/benni_media_apply_app"
FRONTEND_ENTRY: Final[str] = f"{FRONTEND_DIR_URL}/main.js"


def unique_id(entry_id: str, suffix: str) -> str:
    """Domain- + entry-scoped unique_id (core_state-Blaupause, kollisionsfrei)."""
    return f"{DOMAIN}_{entry_id}_{suffix}"


# --------------------------------------------------------------------------- #
# Profil-Hub (benni / eltern) + Auto-Bind: options ▶ data ▶ Profil-Map ▶ leer.
# --------------------------------------------------------------------------- #
CONF_PROFILE: Final[str] = "profile"
PROFILE_BENNI: Final[str] = "benni"
PROFILE_ELTERN: Final[str] = "eltern"
PROFILES: Final[list[str]] = [PROFILE_BENNI, PROFILE_ELTERN]
DEFAULT_PROFILE: Final[str] = PROFILE_BENNI
PROFILE_LABELS: Final[dict[str, str]] = {PROFILE_BENNI: "Benni", PROFILE_ELTERN: "Eltern"}

# --------------------------------------------------------------------------- #
# Action-Contract (KOPIE aus media_policy — kein Import).
# --------------------------------------------------------------------------- #
ACTION_NONE: Final = "none"
ACTION_PAUSE: Final = "pause_homepods"
ACTION_RESUME: Final = "resume_homepods"
ACTION_START_RADIO: Final = "start_radio"

# Ausführungs-Modus pro Tick (R2/R3): wie der berechnete Plan zum Gerät kommt.
EXEC_SHADOW: Final = "shadow"        # apply_enabled aus → gar nicht ausführen.
EXEC_IMMEDIATE: Final = "immediate"  # Quiet bricht sofort durch (kein Debounce).
EXEC_DEBOUNCE: Final = "debounce"    # Normalfall → 5s-Fenster konsolidiert Bursts.

# media_player-Zustände, die als "spielt" gelten.
PLAYER_PLAYING_VALUES: Final = ("playing",)
# Zustände, in denen ein Volume-Set sinnvoll ist (nicht unknown/unavailable/off).
PLAYER_ADDRESSABLE_VALUES: Final = ("playing", "idle", "paused", "on", "buffering")
# media_player/AVR-Zustände, die als "ausgeschaltet" gelten (Denon-Power-Ableitung,
# falls kein dediziertes Power-Atomic gebunden ist).
PLAYER_OFF_VALUES: Final = ("off", "standby")

# Bio-State (core_state), bei dem R14 pausiert (Sleep dominant).
BIO_SLEEP_VALUE: Final = "sleep"

# --------------------------------------------------------------------------- #
# Config-Keys — Eingänge (via Entity-State).
# --------------------------------------------------------------------------- #
# aus media_policy:
CONF_AUDIO_OWNER: Final[str] = "audio_owner_entity"
CONF_ACTION: Final[str] = "action_entity"
CONF_VOLUME_POLICY: Final[str] = "volume_policy_entity"
CONF_VOL_TARGET_HOMEPODS: Final[str] = "volume_target_homepods_entity"
CONF_VOL_TARGET_DENON: Final[str] = "volume_target_denon_entity"
CONF_HOMEPODS_SHOULD_PAUSE: Final[str] = "homepods_should_pause_entity"
CONF_HOMEPODS_RESUME_ALLOWED: Final[str] = "homepods_resume_allowed_entity"
CONF_SUBWOOFER_ALLOWED: Final[str] = "subwoofer_allowed_entity"
CONF_VOLUME_APPLY_ALLOWED: Final[str] = "volume_apply_allowed_entity"
# aus media_state:
CONF_QUIET_MODE: Final[str] = "quiet_mode_entity"
# Stop-Latch (shared Helper):
CONF_STOP_LATCH: Final[str] = "stop_latch_entity"
# Geräte (Apply-Targets):
CONF_HOMEPODS_PLAYER: Final[str] = "homepods_player_entity"
CONF_DENON_PLAYER: Final[str] = "denon_player_entity"
CONF_SUBWOOFER_SWITCH: Final[str] = "subwoofer_switch_entity"

# --------------------------------------------------------------------------- #
# Phase 3 (R13/R14 Denon-Nachlauf) — Geräte-Power-Inputs.
# PC-/TV-Power sind core_devices-Atomics, die FLEET-54 gerade migriert → Bindings
# bleiben hier DEFERRED (PROFILE_PREFILL leer), bis die Atomic-Slugs feststehen.
# Bis dahin liefern sie None ⇒ die Nachlauf-Timer armen nie (no-op, doppelt safe
# zusätzlich zum Shadow-Gate). Denon-Power leitet sich notfalls aus dem bereits
# gebundenen CONF_DENON_PLAYER ab; bio_state kommt aus core_state (stabil).
# --------------------------------------------------------------------------- #
CONF_PC_POWER: Final[str] = "pc_power_entity"
CONF_TV_POWER: Final[str] = "tv_power_entity"
CONF_DENON_POWER: Final[str] = "denon_power_entity"
CONF_BIO_STATE: Final[str] = "bio_state_entity"

# Keys, deren gebundene Entities der Coordinator beobachtet (event-driven).
WATCH_KEYS: Final[tuple[str, ...]] = (
    CONF_AUDIO_OWNER, CONF_ACTION, CONF_VOLUME_POLICY,
    CONF_VOL_TARGET_HOMEPODS, CONF_VOL_TARGET_DENON,
    CONF_HOMEPODS_SHOULD_PAUSE, CONF_HOMEPODS_RESUME_ALLOWED,
    CONF_SUBWOOFER_ALLOWED, CONF_VOLUME_APPLY_ALLOWED,
    CONF_QUIET_MODE, CONF_STOP_LATCH,
    CONF_HOMEPODS_PLAYER, CONF_DENON_PLAYER, CONF_SUBWOOFER_SWITCH,
    CONF_PC_POWER, CONF_TV_POWER, CONF_DENON_POWER, CONF_BIO_STATE,
)
ENTITY_SLOT_KEYS: Final[tuple[str, ...]] = WATCH_KEYS

# --------------------------------------------------------------------------- #
# Profil-Map (Auto-Bind). benni = Live-IDs der Einhornzentrale. Existenz-Filter
# regelt Fehlendes. eltern leer (Anlage existiert noch nicht).
# --------------------------------------------------------------------------- #
PROFILE_PREFILL: Final[dict[str, dict[str, Any]]] = {
    PROFILE_BENNI: {
        CONF_AUDIO_OWNER: "sensor.benni_media_policy_audio_owner",
        CONF_ACTION: "sensor.benni_media_policy_action",
        CONF_VOLUME_POLICY: "sensor.benni_media_policy_volume_policy",
        CONF_VOL_TARGET_HOMEPODS: "sensor.benni_media_policy_volume_target_homepods",
        CONF_VOL_TARGET_DENON: "sensor.benni_media_policy_volume_target_denon",
        CONF_HOMEPODS_SHOULD_PAUSE: "binary_sensor.benni_media_policy_homepods_should_pause",
        CONF_HOMEPODS_RESUME_ALLOWED: "binary_sensor.benni_media_policy_homepods_resume_allowed",
        CONF_SUBWOOFER_ALLOWED: "binary_sensor.benni_media_policy_subwoofer_allowed",
        CONF_VOLUME_APPLY_ALLOWED: "binary_sensor.benni_media_policy_volume_apply_allowed",
        CONF_QUIET_MODE: "binary_sensor.benni_media_state_quiet_mode",
        CONF_STOP_LATCH: "input_boolean.media_stop_latch",
        CONF_HOMEPODS_PLAYER: "media_player.living_homepods_ma_group",
        CONF_DENON_PLAYER: "media_player.living_denon",
        CONF_SUBWOOFER_SWITCH: "switch.living_subwoofer_plug",
        # Phase 3 — DEFERRED bis FLEET-54 die Atomic-Slugs festklopft (leer = no-op).
        # Kandidaten (zur Bindung nach #54): PC-Power-Atomic, TV-Power-Atomic
        # (WebOS/Wattage, R11), denon_power → binary_sensor.living_denon_plug_power_active_atomic,
        # bio_state → sensor.<...>_core_state_bio_state.
        # Post-FLEET-54: an core_devices/core_state gebunden (Denon-Nachlauf R13/R14).
        CONF_PC_POWER: "sensor.benni_device_living_pc",
        CONF_TV_POWER: "sensor.benni_device_living_tv",
        CONF_DENON_POWER: "",  # leer = aus Denon-Player abgeleitet (sicherer als avr-Statemix)
        CONF_BIO_STATE: "sensor.benni_core_state_bio_state",
    },
    PROFILE_ELTERN: {},
}

# --------------------------------------------------------------------------- #
# Options — Apply-Gate + Ramp-Settings (§6, konfigurierbar).
# --------------------------------------------------------------------------- #
CONF_APPLY_ENABLED: Final[str] = "apply_enabled"
DEFAULT_APPLY_ENABLED: Final[bool] = False   # Shadow-safe out of the box.

CONF_RAMP_STEPS: Final[str] = "ramp_steps"
CONF_RAMP_STEP_DELAY: Final[str] = "ramp_step_delay_seconds"
CONF_TINY_DELTA: Final[str] = "tiny_delta"
CONF_DUCKED_LEVEL: Final[str] = "ducked_level"
# R2 — Debounce: Szenario-Übergänge warten dieses Fenster, Trigger-Bursts werden
# zu EINER Aktion konsolidiert. Quiet bricht durch (kein Debounce). 5s kalibrierbar.
CONF_DEBOUNCE_SECONDS: Final[str] = "debounce_seconds"
# Service-Delegation für start_radio (Radio-Katalog bleibt vorerst YAML).
CONF_RADIO_START_SCRIPT: Final[str] = "radio_start_script"

DEFAULT_RAMP_STEPS: Final[int] = 16
DEFAULT_RAMP_STEP_DELAY: Final[float] = 1.0
DEFAULT_TINY_DELTA: Final[float] = 0.02
DEFAULT_DUCKED_LEVEL: Final[float] = 0.10
DEFAULT_DEBOUNCE_SECONDS: Final[float] = 5.0
DEFAULT_RADIO_START_SCRIPT: Final[str] = "script.media_radio_start"

# Phase 3 — Denon-Nachlauf (R13/R14), Sekunden (Lastenheft 20_helpers: 90s).
CONF_DENON_NACHLAUF_PC: Final[str] = "denon_nachlauf_pc_seconds"
CONF_DENON_NACHLAUF_TV: Final[str] = "denon_nachlauf_tv_seconds"
DEFAULT_DENON_NACHLAUF_PC: Final[float] = 90.0
DEFAULT_DENON_NACHLAUF_TV: Final[float] = 90.0

RAMP_SETTING_DEFAULTS: Final[dict[str, Any]] = {
    CONF_RAMP_STEPS: DEFAULT_RAMP_STEPS,
    CONF_RAMP_STEP_DELAY: DEFAULT_RAMP_STEP_DELAY,
    CONF_TINY_DELTA: DEFAULT_TINY_DELTA,
    CONF_DUCKED_LEVEL: DEFAULT_DUCKED_LEVEL,
    CONF_DEBOUNCE_SECONDS: DEFAULT_DEBOUNCE_SECONDS,
}

# --------------------------------------------------------------------------- #
# Status-/Debug-Roster (Output-Entities). Im Shadow zeigt das den geplanten,
# nicht ausgeführten Apply-Plan.
# --------------------------------------------------------------------------- #
UID_LAST_ACTION: Final[str] = "last_action"
UID_HOMEPODS_TARGET: Final[str] = "homepods_target"
UID_DENON_TARGET: Final[str] = "denon_target"
UID_RAMP_ACTIVE: Final[str] = "ramp_active"
UID_APPLY_ENABLED: Final[str] = "apply_enabled"
UID_EXECUTE: Final[str] = "execute"
UID_NACHLAUF_ACTIVE: Final[str] = "denon_nachlauf_active"

DEFAULT_DATA: Final[dict[str, Any]] = {
    "last_action": ACTION_NONE,
    "homepods_target": None,
    "denon_target": None,
    "ramp_active": False,
    "apply_enabled": DEFAULT_APPLY_ENABLED,
    "execute": False,
    "denon_nachlauf_active": False,
}
