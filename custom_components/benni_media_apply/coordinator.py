"""Media-Apply-Coordinator (Single-Instance, event-driven Executor).

DataUpdateCoordinator ohne Polling: rechnet bei State-Changes der gebundenen
Quell-Entities den Apply-Plan neu (logic.decide_apply) und FÜHRT ihn aus —
idempotent, mit abbrechbarem HomePods-Ramp-Task (16×1s; Quiet bricht durch).

Apply-Gate: `apply_enabled` (Option, Shadow-Kill-Switch) × `volume_apply_allowed`
(pro Entscheidung, aus media_policy). Im Shadow wird der Plan berechnet + als
Status-/Debug-Sensoren exponiert, aber NICHT ausgeführt.

start_radio wird (Phase 1) an ein Script delegiert (Radio-Katalog bleibt YAML).
"""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import replace
from typing import Any, Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from . import logic
from .const import (
    ACTION_NONE,
    ACTION_DENON_OFF,
    ACTION_PAUSE,
    ACTION_RESUME,
    ACTION_START_RADIO,
    BIO_AWAKE_VALUES,
    BIO_SLEEP_VALUE,
    CONF_ACTION,
    CONF_APPLY_ENABLED,
    CONF_AWAY_GATE,
    CONF_BIO_STATE,
    CONF_DEBOUNCE_SECONDS,
    CONF_DENON_NACHLAUF_PC,
    CONF_DENON_NACHLAUF_TV,
    CONF_DENON_PLAYER,
    CONF_DENON_POWER,
    CONF_DUCKED_LEVEL,
    CONF_HOMEPODS_PLAYER,
    CONF_HOMEPODS_RESUME_ALLOWED,
    CONF_HOMEPODS_SHOULD_PAUSE,
    CONF_MANUAL_PLAYBACK,
    CONF_MEDIA_DEVICE,
    CONF_PC_POWER,
    CONF_PLANNED_STATION_PLAYING,
    CONF_PRESENCE_STATE,
    CONF_PRIVATE_MANUAL,
    CONF_PRIVATE_MANUAL_TIMEOUT,
    CONF_PROFILE,
    CONF_QUIET_MODE,
    CONF_RADIO_AUTOSTART,
    CONF_RADIO_RESUME_DELAY,
    CONF_SLEEP_TV_EXTEND,
    CONF_SLEEP_TV_NOTIFY,
    CONF_SLEEP_TV_OFF_DELAY,
    CONF_SLEEP_TV_WARN_LEAD,
    CONF_SLEEP_TV_WARN_MESSAGE,
    CONF_RADIO_PLAY_DELAY,
    CONF_RADIO_READY,
    CONF_RADIO_START_SCRIPT,
    CONF_RADIO_STATION,
    CONF_RAMP_STEP_DELAY,
    CONF_RAMP_STEPS,
    CONF_STOP_LATCH,
    CONF_SUBWOOFER_ALLOWED,
    CONF_SUBWOOFER_SWITCH,
    CONF_TINY_DELTA,
    CONF_TV_PLAYER,
    CONF_TV_POWER,
    CONF_TV_WOL_MAC,
    CONF_WAKE_DEBOUNCE,
    CONF_WAKE_PLAY_LEAD,
    CONF_WAKE_START_VOLUME,
    CONF_WAKE_TRIGGERS,
    CONF_VOL_TARGET_DENON,
    CONF_VOL_TARGET_HOMEPODS,
    CONF_VOLUME_APPLY_ALLOWED,
    DEFAULT_APPLY_ENABLED,
    DEFAULT_DEBOUNCE_SECONDS,
    DEFAULT_DENON_NACHLAUF_PC,
    DEFAULT_DENON_NACHLAUF_TV,
    DEFAULT_DUCKED_LEVEL,
    DEFAULT_PRIVATE_MANUAL_TIMEOUT,
    DEFAULT_PROFILE,
    DEFAULT_RADIO_PLAY_DELAY,
    DEFAULT_RADIO_AUTOSTART,
    DEFAULT_RADIO_RESUME_DELAY,
    DEFAULT_RADIO_SEARCH_LIMIT,
    DEFAULT_RADIO_START_SCRIPT,
    DEFAULT_SLEEP_TV_NOTIFY,
    DEFAULT_SLEEP_TV_OFF_DELAY,
    DEFAULT_SLEEP_TV_WARN_LEAD,
    DEFAULT_SLEEP_TV_WARN_MESSAGE,
    DEFAULT_TV_WOL_MAC,
    DEFAULT_WAKE_DEBOUNCE,
    DEFAULT_WAKE_PLAY_LEAD,
    DEFAULT_WAKE_START_VOLUME,
    DEFAULT_RAMP_STEP_DELAY,
    DEFAULT_RAMP_STEPS,
    DEFAULT_TINY_DELTA,
    DENON_CONSUMER_DEVICES,
    DOMAIN,
    EXEC_IMMEDIATE,
    EXEC_SHADOW,
    PLAYER_OFF_VALUES,
    RADIO_ENQUEUE,
    RADIO_MEDIA_TYPE,
    SCREEN_DEVICES,
    PROFILE_PREFILL,
    PROFILES,
    WATCH_KEYS,
)

_LOGGER = logging.getLogger(__name__)

_TRUE = frozenset({"on", "true", "1", "home", "active", "playing", "open"})


def _bool(s: str | None) -> bool:
    return s is not None and s.lower() in _TRUE


class MediaApplyCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Eine Instanz pro Config-Entry (Single-Instance-Modell)."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=None)
        self.entry = entry
        profile = entry.data.get(CONF_PROFILE, DEFAULT_PROFILE)
        self._profile = profile if profile in PROFILES else DEFAULT_PROFILE
        self._unsub_state = None
        self._ramp_task = None
        self._ramp_active = False
        # R2/R3 — Debounce-Fenster + serialisierte Ausführung (latest-wins).
        self._debounce_unsub = None
        self._debounce_deadline: Optional[float] = None   # loop.time(), für remaining_s
        self._pending_plan: Optional[logic.ApplyPlan] = None
        self._exec_lock = asyncio.Lock()
        self._apply_state = logic.ApplyState()
        self._nachlauf_state = logic.NachlaufState()
        self._tv_wol_state = logic.TvWolState()
        self._sleep_tv_state = logic.SleepTvState()
        self._sleep_tv_task: Optional[asyncio.Task] = None
        self._last_extend_state: str | None = None
        self._wake_task: Optional[asyncio.Task] = None
        self._last_wake_states: dict[str, bool] = {}
        self._last_bio_state: str | None = None
        self._radio_resume_task: Optional[asyncio.Task] = None
        self._last_manual_playback: bool | None = None
        self._last_homepods_action: str | None = None
        self._private_task: Optional[asyncio.Task] = None   # FLEET-98 Timeout-Timer
        self._last_private_manual: bool | None = None
        self._nachlauf_tasks: dict[str, asyncio.Task] = {}
        self._last_debug: dict[str, Any] = {}
        # Observability (FLEET-46): Ramp-Fortschritt + Apply-Log-Ringpuffer.
        self._ramp_step = 0
        self._ramp_total = 0
        self._log: deque[dict[str, Any]] = deque(maxlen=20)
        self._last_log_sig: tuple | None = None

    # ----- profile / binding -----
    @property
    def profile(self) -> str:
        return self._profile

    @property
    def _opts(self) -> dict[str, Any]:
        return {**self.entry.data, **self.entry.options}

    @property
    def apply_enabled(self) -> bool:
        return bool(self._opts.get(CONF_APPLY_ENABLED, DEFAULT_APPLY_ENABLED))

    @property
    def _radio_autostart_enabled(self) -> bool:
        return bool(self._opts.get(CONF_RADIO_AUTOSTART, DEFAULT_RADIO_AUTOSTART))

    def _entity_id(self, key: str) -> Any:
        """Auto-Bind (core_state-Blaupause): options ▶ data ▶ PROFILE_PREFILL."""
        return (
            self.entry.options.get(key)
            or self.entry.data.get(key)
            or PROFILE_PREFILL.get(self._profile, {}).get(key)
        )

    def _watched_entities(self) -> list[str]:
        ids: list[str] = []
        for key in WATCH_KEYS:
            val = self._entity_id(key)
            if isinstance(val, str) and val:
                ids.append(val)
            elif isinstance(val, (list, tuple)):   # Multi-Entity (Wake-Trigger)
                ids.extend(e for e in val if isinstance(e, str) and e)
        return list(dict.fromkeys(ids))

    def bindings(self) -> dict[str, Any]:
        return {key: self._entity_id(key) for key in WATCH_KEYS}

    def settings(self) -> logic.RampSettings:
        def _f(key: str, default: float) -> float:
            try:
                return float(self._opts.get(key, default))
            except (TypeError, ValueError):
                return default

        def _i(key: str, default: int) -> int:
            try:
                return int(self._opts.get(key, default))
            except (TypeError, ValueError):
                return default

        return logic.RampSettings(
            ramp_steps=_i(CONF_RAMP_STEPS, DEFAULT_RAMP_STEPS),
            ramp_step_delay_s=_f(CONF_RAMP_STEP_DELAY, DEFAULT_RAMP_STEP_DELAY),
            tiny_delta=_f(CONF_TINY_DELTA, DEFAULT_TINY_DELTA),
            ducked_level=_f(CONF_DUCKED_LEVEL, DEFAULT_DUCKED_LEVEL),
            debounce_seconds=_f(CONF_DEBOUNCE_SECONDS, DEFAULT_DEBOUNCE_SECONDS),
            wake_start_volume=_f(CONF_WAKE_START_VOLUME, DEFAULT_WAKE_START_VOLUME),
            wake_debounce_seconds=_f(CONF_WAKE_DEBOUNCE, DEFAULT_WAKE_DEBOUNCE),
        )

    # ----- lifecycle -----
    @callback
    def async_start(self) -> None:
        watched = self._watched_entities()
        if watched:
            self._unsub_state = async_track_state_change_event(
                self.hass, watched, self._on_state_change
            )
            self.entry.async_on_unload(self._unsub_state)

    @callback
    def _on_state_change(self, _event: Event) -> None:
        self.async_set_updated_data(self._compute())

    @callback
    def async_shutdown_ramp(self) -> None:
        """Unload-Hook: laufende Ramp-, Debounce- und Nachlauf-Tasks abbrechen."""
        self._cancel_ramp()
        self._cancel_debounce()
        self._cancel_sleep_tv()
        self._cancel_wake()
        self._cancel_radio_resume()
        self._cancel_private_timeout()
        for key in list(self._nachlauf_tasks):
            self._cancel_nachlauf(key)

    # ----- reads -----
    def _state(self, key: str) -> str | None:
        eid = self._entity_id(key)
        if not eid:
            return None
        st = self.hass.states.get(eid)
        if st is None or st.state in ("unknown", "unavailable"):
            return None
        return st.state

    def _attr_float(self, key: str, attr: str) -> Optional[float]:
        eid = self._entity_id(key)
        if not eid:
            return None
        st = self.hass.states.get(eid)
        if st is None:
            return None
        try:
            return float(st.attributes.get(attr))
        except (TypeError, ValueError):
            return None

    def _float(self, key: str) -> Optional[float]:
        raw = self._state(key)
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    def _tri_bool(self, key: str) -> Optional[bool]:
        """Tri-state: None wenn ungebunden ODER Zustand unbekannt/unavailable,
        sonst bool. Verhindert, dass Nachlauf-Timer auf fehlenden Daten armen."""
        if not self._entity_id(key):
            return None
        raw = self._state(key)   # None bei unknown/unavailable
        if raw is None:
            return None
        return _bool(raw)

    def _powered(self, key: str) -> Optional[bool]:
        """Power-Wahrheit eines core_devices-Geräts: bevorzugt Attribute
        (`powered`, `is_active`, `watt_active`) und fällt dann auf den
        bool-kompatiblen State zurück. None = ungebunden/
        unbekannt (FLEET-80: verhindert falsche Nachlauf-Arms auf „idle")."""
        eid = self._entity_id(key)
        if not eid:
            return None
        st = self.hass.states.get(eid)
        if st is None or st.state in ("unknown", "unavailable"):
            return None
        for attr in ("powered", "is_active", "watt_active"):
            value = st.attributes.get(attr)
            if isinstance(value, bool):
                return value
            if value is not None:
                return _bool(str(value))
        return _bool(st.state)

    def _denon_consumer_active(self) -> Optional[bool]:
        """FLEET-80 Cross-Source-Gate: Ist ein Denon-Konsument (media_device ∈
        DENON_CONSUMER_DEVICES) aktiv? `denon`/`homepods`/`none` zählen NICHT.
        None wenn media_device ungebunden/unbekannt ⇒ konservativ (kein Off)."""
        if not self._entity_id(CONF_MEDIA_DEVICE):
            return None
        md = self._state(CONF_MEDIA_DEVICE)   # None bei unknown/unavailable
        if md is None:
            return None
        return md in DENON_CONSUMER_DEVICES

    def _denon_power_on(self) -> Optional[bool]:
        """Denon-Power: dediziertes Atomic bevorzugt (CONF_DENON_POWER, sobald
        nach #54 gebunden), sonst Ableitung aus dem bereits gebundenen
        Denon-media_player (state nicht in off/standby)."""
        if self._entity_id(CONF_DENON_POWER):
            return self._tri_bool(CONF_DENON_POWER)
        st = self._state(CONF_DENON_PLAYER)
        if st is None:
            return None
        return st not in PLAYER_OFF_VALUES

    def _bio_sleep(self) -> Optional[bool]:
        """bio_state == 'sleep' (core_state). None wenn ungebunden/unbekannt."""
        if not self._entity_id(CONF_BIO_STATE):
            return None
        st = self._state(CONF_BIO_STATE)
        if st is None:
            return None
        return st == BIO_SLEEP_VALUE

    # ----- evaluation -----
    def _build_inputs(self) -> logic.Inputs:
        return logic.Inputs(
            apply_enabled=self.apply_enabled,
            volume_apply_allowed=_bool(self._state(CONF_VOLUME_APPLY_ALLOWED)),
            action=self._state(CONF_ACTION) or "none",
            homepods_should_pause=_bool(self._state(CONF_HOMEPODS_SHOULD_PAUSE)),
            homepods_resume_allowed=_bool(self._state(CONF_HOMEPODS_RESUME_ALLOWED)),
            homepods_target=self._float(CONF_VOL_TARGET_HOMEPODS),
            denon_target=self._float(CONF_VOL_TARGET_DENON),
            subwoofer_allowed=_bool(self._state(CONF_SUBWOOFER_ALLOWED)),
            quiet_mode=_bool(self._state(CONF_QUIET_MODE)),
            presence_state=self._state(CONF_PRESENCE_STATE),
            presence_degraded=bool(self._entity_id(CONF_PRESENCE_STATE))
            and self._state(CONF_PRESENCE_STATE) is None,
            away_gate=self._tri_bool(CONF_AWAY_GATE),
            stop_latch=_bool(self._state(CONF_STOP_LATCH)),
            radio_station=self._state(CONF_RADIO_STATION),
            radio_ready=self._tri_bool(CONF_RADIO_READY),
            manual_playback=self._tri_bool(CONF_MANUAL_PLAYBACK),
            planned_station_playing=self._tri_bool(CONF_PLANNED_STATION_PLAYING),
            homepods_configured=bool(self._entity_id(CONF_HOMEPODS_PLAYER)),
            homepods_state=self._state(CONF_HOMEPODS_PLAYER),
            homepods_volume=self._attr_float(CONF_HOMEPODS_PLAYER, "volume_level"),
            denon_configured=bool(self._entity_id(CONF_DENON_PLAYER)),
            denon_state=self._state(CONF_DENON_PLAYER),
            denon_volume=self._attr_float(CONF_DENON_PLAYER, "volume_level"),
            subwoofer_configured=bool(self._entity_id(CONF_SUBWOOFER_SWITCH)),
            subwoofer_state=self._state(CONF_SUBWOOFER_SWITCH),
            # Phase 3 (R13/R14): watt-primäres `powered`-Attribut (FLEET-80) statt
            # State-String — „idle" bei OLED-Watt-Dip darf nicht als aus zählen.
            pc_power_on=self._powered(CONF_PC_POWER),
            tv_power_on=self._powered(CONF_TV_POWER),
            denon_power_on=self._denon_power_on(),
            bio_sleep=self._bio_sleep(),
            # FLEET-80 Cross-Source-Gate: anderer Denon-Konsument aktiv?
            denon_consumer_active=self._denon_consumer_active(),
            # Phase 4c (R12 TV-WoL).
            media_device=self._state(CONF_MEDIA_DEVICE),
            tv_player_state=self._state(CONF_TV_PLAYER),
        )

    def _compute(self) -> dict[str, Any]:
        inputs = self._build_inputs()
        media_blocked = logic.media_block_reason(inputs) is not None
        if media_blocked:
            self._cancel_radio_resume()
            self._cancel_wake()
        plan, self._apply_state = logic.decide_apply(
            inputs, self._apply_state, self.settings()
        )
        previous_homepods_action = self._last_homepods_action
        self._last_homepods_action = plan.homepods_action
        if (
            plan.homepods_action == ACTION_START_RADIO
            and previous_homepods_action in (None, ACTION_START_RADIO)
        ):
            plan = logic.suppress_start_radio_action(
                plan, "startup_or_repeated:start_radio_suppressed"
            )
        nplan, self._nachlauf_state = logic.decide_denon_nachlauf(
            inputs, self._nachlauf_state
        )
        twol, self._tv_wol_state = logic.decide_tv_wol(inputs, self._tv_wol_state)
        bio_to_awake, bio_to_sleep = self._bio_edges()
        edge_inp = replace(
            inputs,
            sleep_tv_extend_pressed=self._consume_extend_edge(),
            wake_trigger_fired=self._wake_trigger_fired(bio_to_awake),
        )
        splan, self._sleep_tv_state = logic.decide_sleep_tv(edge_inp, self._sleep_tv_state)
        wplan = logic.decide_wake(edge_inp)
        self._last_debug = {
            **plan.as_dict(), "nachlauf": nplan.as_dict(),
            "tv_wol": twol.as_dict(), "sleep_tv": splan.as_dict(),
            "wake": wplan.as_dict(),
        }
        self._maybe_log(plan)
        # R2/R3: Ausführung läuft über Debounce-Fenster + Serialisierung, Quiet
        # bricht sofort durch. Preview/Status (oben) aktualisieren sich pro Event.
        self._schedule_execute(plan)
        # Nachlauf-Flanken IMMER verarbeiten (Arm/Cancel-Buchwerk auch im Shadow,
        # für Observability); der reale Denon-Off ist in _run_nachlauf gegatet.
        if nplan.active:
            self._apply_nachlauf(nplan)
        # R12 TV-WoL: SOFORT (kein Debounce), aber apply-gated (automatische Aktion).
        if twol.fire and self.apply_enabled:
            self.hass.async_create_task(self._execute_tv_wol())
        # R24 Sleep-TV-Off: Timer-Flanken IMMER verarbeiten (Arm/Cancel-Buchwerk
        # auch im Shadow, für Observability); der reale TV-Off ist gegatet.
        if splan.intent in (logic.TIMER_ARM, logic.TIMER_EXTEND):
            self._schedule_sleep_tv()
        elif splan.intent == logic.TIMER_CANCEL:
            self._cancel_sleep_tv()
        # R23 Wake-Sequenz: Trigger-Flanke → HomePods 0.10 → Debounce → Ramp auf Ziel.
        if wplan.fire and self.apply_enabled:
            self._schedule_wake()
        # FLEET-79 Radio-Autostart (Port der disabled YAML-Automationen).
        manual_off = self._manual_off_edge()
        if self.apply_enabled and self._radio_autostart_enabled and not media_blocked:
            if wplan.fire and logic.should_autostart_radio(inputs):
                # Trigger A: Wake → Latch lösen + geplante Station starten.
                self.hass.async_create_task(self._run_radio_autostart())
            elif manual_off and inputs.action != ACTION_PAUSE and logic.should_autostart_radio(inputs):
                # Trigger B: manuelle Wiedergabe endete → nach Delay fortsetzen.
                self._schedule_radio_resume()
        # FLEET-98: manuellen private_time-Latch auto-löschen (bio→sleep ODER Timeout).
        self._handle_private_manual(bio_to_sleep)
        return {
            "last_action": plan.homepods_action,
            "homepods_target": plan.homepods_levels[-1] if plan.homepods_levels else None,
            "denon_target": plan.denon_set,
            "ramp_active": self._ramp_active,
            "apply_enabled": self.apply_enabled,
            "execute": plan.execute,
            "denon_nachlauf_active": (
                self._nachlauf_state.pc_armed or self._nachlauf_state.tv_armed
            ),
        }

    async def _async_update_data(self) -> dict[str, Any]:
        return self._compute()

    # ----- R2/R3: Debounce + serialisierte Ausführung -----
    @callback
    def _schedule_execute(self, plan: "logic.ApplyPlan") -> None:
        """Leitet einen Plan in die Ausführung (R2/R3). Quiet bricht sofort durch,
        sonst sammelt ein Debounce-Fenster Trigger-Bursts zu EINER Aktion."""
        mode = logic.execution_mode(plan)
        if mode == EXEC_SHADOW:
            # Apply (wieder) aus → kein Pending mehr ausführen.
            self._cancel_debounce()
            self._pending_plan = None
            return
        if mode == EXEC_IMMEDIATE:
            # Quiet: laufendes Fenster verwerfen, sofort (serialisiert) ducken.
            self._cancel_debounce()
            self._pending_plan = plan
            self.hass.async_create_task(self._execute_serialized())
            return
        # EXEC_DEBOUNCE — triviale Re-Evals dürfen ein laufendes Fenster nicht
        # neu anstoßen (sonst hungert ein gepufferter echter Plan aus).
        if not plan.has_work:
            return
        self._pending_plan = plan
        self._start_debounce()

    @callback
    def _start_debounce(self) -> None:
        self._cancel_debounce()
        window = self.settings().debounce_seconds
        self._debounce_deadline = self.hass.loop.time() + window
        self._debounce_unsub = async_call_later(self.hass, window, self._fire_debounce)

    @callback
    def _cancel_debounce(self) -> None:
        if self._debounce_unsub is not None:
            self._debounce_unsub()
            self._debounce_unsub = None
        self._debounce_deadline = None

    @callback
    def _fire_debounce(self, _now) -> None:
        self._debounce_unsub = None
        self._debounce_deadline = None
        self.hass.async_create_task(self._execute_serialized())

    def _debounce_remaining(self) -> Optional[float]:
        """Restzeit bis das Fenster feuert (Sekunden), None wenn kein Fenster läuft."""
        if self._debounce_deadline is None:
            return None
        return round(max(0.0, self._debounce_deadline - self.hass.loop.time()), 2)

    async def _execute_serialized(self) -> None:
        """Serialisiert die Geräte-Schaltung (R3: Queue statt Race). Es läuft
        immer der zuletzt gepufferte Plan (idempotent → latest-wins); ein zweiter
        wartender Task findet None vor und ist ein No-op."""
        async with self._exec_lock:
            plan = self._pending_plan
            self._pending_plan = None
            if plan is None:
                return
            await self._execute(plan)

    def _maybe_log(self, plan: "logic.ApplyPlan") -> None:
        """Apply-Log-Ringpuffer: jede nicht-triviale Plan-Änderung mit Timestamp +
        execute-Flag (Shadow-Entscheidungen inklusive, für Observability)."""
        hp_target = plan.homepods_levels[-1] if plan.homepods_levels else None
        trivial = (
            plan.homepods_action == ACTION_NONE
            and not plan.homepods_levels
            and plan.denon_set is None
            and plan.subwoofer_set is None
            and not plan.quiet_override
        )
        sig = (plan.homepods_action, hp_target, plan.denon_set, plan.subwoofer_set,
               plan.quiet_override, plan.execute)
        if trivial or sig == self._last_log_sig:
            return
        self._last_log_sig = sig
        self._log.appendleft({
            "ts": dt_util.utcnow().isoformat(),
            "action": plan.homepods_action,
            "homepods_target": hp_target,
            "denon_target": plan.denon_set,
            "subwoofer_set": plan.subwoofer_set,
            "quiet": plan.quiet_override,
            "executed": plan.execute,
        })

    def status(self) -> dict[str, Any]:
        """Konsolidierter Apply-Status für Panel/Umbrella (WS-Contract = das
        Bleibende). Read-only: nur ein frischer Inputs-Snapshot, keine Neuberechnung
        des Plans (der kommt aus dem letzten Tick)."""
        inp = self._build_inputs()
        s = self.settings()
        plan = {k: v for k, v in self._last_debug.items() if k != "nachlauf"}
        execute = bool((self.data or {}).get("execute", False))
        return {
            "profile": self._profile,
            "apply_enabled": self.apply_enabled,
            "execute": execute,
            "ramp_active": self._ramp_active,
            "ramp_step": self._ramp_step,
            "ramp_total": self._ramp_total,
            "debounce": {
                "window_s": s.debounce_seconds,
                "pending": self._debounce_unsub is not None,
                "remaining_s": self._debounce_remaining(),
                # Der eine konsolidierte, noch nicht ausgeführte Plan (latest-wins,
                # KEINE Stale-FIFO) — Cockpit zeigt damit „was als Nächstes käme".
                "plan": self._pending_plan.as_dict() if self._pending_plan else None,
            },
            "plan": plan,
            "log": list(self._log),
            "gates": {
                "apply_enabled": self.apply_enabled,
                "volume_apply_allowed": inp.volume_apply_allowed,
                "execute": execute,
                "stop_latch": inp.stop_latch,
            },
            "policy": {
                "action": inp.action,
                "homepods_should_pause": inp.homepods_should_pause,
                "homepods_resume_allowed": inp.homepods_resume_allowed,
                "homepods_target": inp.homepods_target,
                "denon_target": inp.denon_target,
                "subwoofer_allowed": inp.subwoofer_allowed,
                "quiet_mode": inp.quiet_mode,
            },
            "devices": {
                "homepods": {"configured": inp.homepods_configured, "state": inp.homepods_state, "volume": inp.homepods_volume},
                "denon": {"configured": inp.denon_configured, "state": inp.denon_state, "volume": inp.denon_volume, "power_on": inp.denon_power_on},
                "subwoofer": {"configured": inp.subwoofer_configured, "state": inp.subwoofer_state},
            },
            "nachlauf": {
                "active": self._nachlauf_state.pc_armed or self._nachlauf_state.tv_armed,
                "pc_armed": self._nachlauf_state.pc_armed,
                "tv_armed": self._nachlauf_state.tv_armed,
                "tv_paused": self._nachlauf_state.tv_paused,
                "pc_power_on": inp.pc_power_on,
                "tv_power_on": inp.tv_power_on,
                "bio_sleep": inp.bio_sleep,
                "denon_consumer_active": inp.denon_consumer_active,
                "tasks": sorted(self._nachlauf_tasks),
            },
            "tv_wol": {
                "fired": self._tv_wol_state.fired,
                "media_device": inp.media_device,
                "tv_player_state": inp.tv_player_state,
                "is_screen": inp.media_device in SCREEN_DEVICES,
                "screen_devices": list(SCREEN_DEVICES),
                "mac": str(self._opts.get(CONF_TV_WOL_MAC, DEFAULT_TV_WOL_MAC) or "") or None,
            },
            "sleep_tv": {
                "armed": self._sleep_tv_state.armed,
                "running": self._sleep_tv_task is not None and not self._sleep_tv_task.done(),
                "bio_sleep": inp.bio_sleep,
                "tv_player_state": inp.tv_player_state,
                "delay_s": self._duration(CONF_SLEEP_TV_OFF_DELAY, DEFAULT_SLEEP_TV_OFF_DELAY),
                "warn_lead_s": self._duration(CONF_SLEEP_TV_WARN_LEAD, DEFAULT_SLEEP_TV_WARN_LEAD),
                "notify": str(self._opts.get(CONF_SLEEP_TV_NOTIFY, DEFAULT_SLEEP_TV_NOTIFY) or "") or None,
                "extend_bound": bool(self._entity_id(CONF_SLEEP_TV_EXTEND)),
            },
            "wake": {
                "running": self._wake_task is not None and not self._wake_task.done(),
                "bio_state": self._state(CONF_BIO_STATE),   # primäre Quelle (core_state)
                "extra_triggers": self._entity_id(CONF_WAKE_TRIGGERS),
                "start_volume": s.wake_start_volume,
                "debounce_s": s.wake_debounce_seconds,
                "bio_sleep": inp.bio_sleep,
            },
            "settings": {
                "ramp_steps": s.ramp_steps,
                "ramp_step_delay_s": s.ramp_step_delay_s,
                "tiny_delta": s.tiny_delta,
                "ducked_level": s.ducked_level,
                "debounce_seconds": s.debounce_seconds,
                "wake_start_volume": s.wake_start_volume,
                "wake_debounce_seconds": s.wake_debounce_seconds,
            },
            # Radio-Shortcuts fürs Cockpit (Defaults; Suche läuft via Action).
            "radio": {
                "defaults": logic.radio_defaults(),
                "autostart_enabled": self._radio_autostart_enabled,
                "ready": inp.radio_ready,
                "manual_playback": inp.manual_playback,
                "planned_station_playing": inp.planned_station_playing,
                "resume_pending": self._radio_resume_task is not None and not self._radio_resume_task.done(),
            },
            # FLEET-98: manueller private_time-Latch + Auto-Clear-Status.
            "private_manual": {
                "active": self._tri_bool(CONF_PRIVATE_MANUAL),
                "timeout_s": self._duration(CONF_PRIVATE_MANUAL_TIMEOUT, DEFAULT_PRIVATE_MANUAL_TIMEOUT),
                "timeout_pending": self._private_task is not None and not self._private_task.done(),
            },
            "bindings": self.bindings(),
        }

    def debug(self) -> dict[str, Any]:
        return {
            **self._last_debug,
            "ramp_active": self._ramp_active,
            "debounce_pending": self._debounce_unsub is not None,
            "nachlauf": {
                "pc_armed": self._nachlauf_state.pc_armed,
                "tv_armed": self._nachlauf_state.tv_armed,
                "tv_paused": self._nachlauf_state.tv_paused,
                "tasks": sorted(self._nachlauf_tasks),
            },
            "bindings": self.bindings(),
        }

    # ----- execution (side effects) -----
    async def _svc(
        self, domain: str, service: str, data: dict[str, Any], blocking: bool = False
    ) -> None:
        try:
            await self.hass.services.async_call(domain, service, data, blocking=blocking)
        except Exception as err:  # noqa: BLE001 — Geräte-Fehler dürfen Apply nicht crashen.
            _LOGGER.warning("media_apply: %s.%s %s failed: %s", domain, service, data, err)

    async def _execute(self, plan: logic.ApplyPlan) -> None:
        hp = self._entity_id(CONF_HOMEPODS_PLAYER)
        denon = self._entity_id(CONF_DENON_PLAYER)
        sub = self._entity_id(CONF_SUBWOOFER_SWITCH)

        if plan.quiet_override or plan.away_block:
            self._cancel_ramp()

        # ----- HomePods-Action -----
        if hp:
            if plan.homepods_action == ACTION_PAUSE:
                await self._svc("media_player", "media_pause", {"entity_id": hp})
            elif plan.homepods_action == ACTION_RESUME:
                await self._svc("media_player", "media_play", {"entity_id": hp})
            elif plan.homepods_action == ACTION_START_RADIO:
                if plan.radio_uri:
                    # Inline-Port (Phase 4b): play_media → kurze Pause → media_play,
                    # wie das YAML-Script. Idempotenz-Gate (nicht schon playing) hat
                    # die Pure-Logic bereits geprüft.
                    await self._svc(
                        "music_assistant", "play_media",
                        {
                            "entity_id": hp, "media_id": plan.radio_uri,
                            "media_type": RADIO_MEDIA_TYPE, "enqueue": RADIO_ENQUEUE,
                        },
                    )
                    self.hass.async_create_task(self._radio_play_after(hp))
                else:
                    # Fallback: Sender ungebunden/unbekannt → YAML-Script delegieren.
                    radio = self._opts.get(CONF_RADIO_START_SCRIPT, DEFAULT_RADIO_START_SCRIPT)
                    await self._svc("script", "turn_on", {"entity_id": radio})

        # ----- HomePods-Volume (Ramp oder direkt) -----
        if plan.homepods_levels and hp:
            self._cancel_ramp()
            if plan.homepods_ramp:
                self._ramp_task = self.hass.async_create_task(
                    self._run_ramp(hp, list(plan.homepods_levels), self.settings().ramp_step_delay_s)
                )
            else:
                await self._svc(
                    "media_player", "volume_set",
                    {"entity_id": hp, "volume_level": plan.homepods_levels[-1]},
                )

        # ----- Denon-Volume (hart) -----
        if plan.denon_set is not None and denon:
            await self._svc(
                "media_player", "volume_set",
                {"entity_id": denon, "volume_level": plan.denon_set},
            )

        # ----- Denon-Aktion -----
        if plan.denon_action == ACTION_DENON_OFF and denon:
            await self._svc("media_player", "turn_off", {"entity_id": denon})

        # ----- Subwoofer-Plug -----
        if plan.subwoofer_set is not None and sub:
            await self._svc("switch", "turn_on" if plan.subwoofer_set else "turn_off", {"entity_id": sub})

    @callback
    def _cancel_ramp(self) -> None:
        if self._ramp_task is not None and not self._ramp_task.done():
            self._ramp_task.cancel()
        self._ramp_task = None
        self._set_ramp_active(False)

    @callback
    def _set_ramp_active(self, active: bool) -> None:
        if self._ramp_active == active:
            return
        self._ramp_active = active
        if not active:
            self._ramp_step = 0
            self._ramp_total = 0
        if self.data is not None:
            self.async_set_updated_data({**self.data, "ramp_active": active})

    async def _run_ramp(self, entity_id: str, levels: list[float], delay: float) -> None:
        """HomePods-Volume-Ramp: Schritt für Schritt mit Delay, abbrechbar."""
        self._ramp_total = len(levels)
        self._set_ramp_active(True)
        try:
            for i, lv in enumerate(levels):
                self._ramp_step = i + 1
                await self.hass.services.async_call(
                    "media_player", "volume_set",
                    {"entity_id": entity_id, "volume_level": lv}, blocking=True,
                )
                await asyncio.sleep(delay)
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("media_apply: ramp on %s failed: %s", entity_id, err)
        finally:
            self._set_ramp_active(False)

    async def _radio_play_after(self, entity_id: str) -> None:
        """play_media füllt nur die Queue; nach kurzer Pause media_play (wie das
        YAML-Script). Geräte-Fehler dürfen Apply nicht crashen."""
        delay = self._duration(CONF_RADIO_PLAY_DELAY, DEFAULT_RADIO_PLAY_DELAY)
        try:
            await asyncio.sleep(max(0.0, delay))
        except asyncio.CancelledError:
            raise
        await self._svc("media_player", "media_play", {"entity_id": entity_id})

    # ----- Radio-Autostart (FLEET-79) -----
    def _manual_off_edge(self) -> bool:
        """manual_playback True→False (Trigger B). Nur EINMAL pro Tick (mutiert)."""
        cur = self._tri_bool(CONF_MANUAL_PLAYBACK)
        prev = self._last_manual_playback
        self._last_manual_playback = cur
        return prev is True and cur is False

    async def _run_radio_autostart(self) -> None:
        """Trigger A: Stop-Latch lösen + geplante Station starten (Wake)."""
        latch = self._entity_id(CONF_STOP_LATCH)
        if latch:
            await self._svc("homeassistant", "turn_off", {"entity_id": latch})
        # Race-Fix: Auf derselben Wake-Flanke setzt _run_wake parallel den
        # Volume-Floor (0.10, blockierend). Kurzer Vorlauf, damit der Floor anliegt,
        # bevor wir Ton ausgeben — sonst Burst bei alter Lautstärke (FLEET-42).
        lead = self._duration(CONF_WAKE_PLAY_LEAD, DEFAULT_WAKE_PLAY_LEAD)
        if lead > 0:
            try:
                await asyncio.sleep(lead)
            except asyncio.CancelledError:
                raise
        if not logic.should_autostart_radio(self._build_inputs()):
            return
        uri = logic.resolve_radio_uri(self._state(CONF_RADIO_STATION))
        if uri:
            await self.async_play_radio(uri)
        else:  # Sender ungebunden/unbekannt → Script-Fallback
            radio = self._opts.get(CONF_RADIO_START_SCRIPT, DEFAULT_RADIO_START_SCRIPT)
            await self._svc("script", "turn_on", {"entity_id": radio})
        _LOGGER.info("media_apply: FLEET-79 Radio-Autostart (wake) → %s", uri or "script")

    @callback
    def _schedule_radio_resume(self) -> None:
        self._cancel_radio_resume()
        self._radio_resume_task = self.hass.async_create_task(self._run_radio_resume())

    @callback
    def _cancel_radio_resume(self) -> None:
        if self._radio_resume_task is not None and not self._radio_resume_task.done():
            self._radio_resume_task.cancel()
        self._radio_resume_task = None

    async def _run_radio_resume(self) -> None:
        """Trigger B: nach Delay die geplante Station fortsetzen — re-prüft die
        Bedingungen (Latch off, ready, kein manual, nicht schon playing)."""
        delay = self._duration(CONF_RADIO_RESUME_DELAY, DEFAULT_RADIO_RESUME_DELAY)
        try:
            await asyncio.sleep(max(0.0, delay))
        except asyncio.CancelledError:
            raise
        self._radio_resume_task = None
        if not (self.apply_enabled and self._radio_autostart_enabled):
            return
        inp = self._build_inputs()
        latch_on = _bool(self._state(CONF_STOP_LATCH))
        if latch_on or not logic.should_autostart_radio(inp) or inp.action == ACTION_PAUSE:
            return
        uri = logic.resolve_radio_uri(inp.radio_station)
        if uri:
            await self.async_play_radio(uri)
            _LOGGER.info("media_apply: FLEET-79 Radio-Resume (post-manual) → %s", uri)

    # ----- private_time manueller Latch: Auto-Clear (FLEET-98) -----
    @callback
    def _handle_private_manual(self, bio_to_sleep: bool) -> None:
        """Manuellen private_time-Latch (input_boolean) auto-löschen:
        (a) bei bio→sleep (du schläfst = kein Dating/Besuch), (b) Fallback nach
        Timeout. Beides apply-gated. on-Flanke startet den Timeout-Timer, off-Flanke
        bricht ab. Nur EINMAL pro Tick (mutiert Vortick-State)."""
        cur = self._tri_bool(CONF_PRIVATE_MANUAL)
        prev = self._last_private_manual
        self._last_private_manual = cur
        if not self.apply_enabled:
            return
        if cur is True and bio_to_sleep:                 # (a) sleep-Clear (Primärpfad)
            self._cancel_private_timeout()
            self.hass.async_create_task(self._clear_private_manual("sleep"))
        elif cur is True and prev is not True:           # on-Flanke → Timeout starten
            self._schedule_private_timeout()
        elif cur is not True and prev is True:           # off-Flanke → Timer aus
            self._cancel_private_timeout()

    @callback
    def _schedule_private_timeout(self) -> None:
        self._cancel_private_timeout()
        timeout = self._duration(CONF_PRIVATE_MANUAL_TIMEOUT, DEFAULT_PRIVATE_MANUAL_TIMEOUT)
        if timeout <= 0:                                  # 0 = nur sleep-Clear
            return
        self._private_task = self.hass.async_create_task(self._run_private_timeout(timeout))

    @callback
    def _cancel_private_timeout(self) -> None:
        if self._private_task is not None and not self._private_task.done():
            self._private_task.cancel()
        self._private_task = None

    async def _run_private_timeout(self, timeout: float) -> None:
        try:
            await asyncio.sleep(max(0.0, timeout))
        except asyncio.CancelledError:
            raise
        self._private_task = None
        if self.apply_enabled and self._tri_bool(CONF_PRIVATE_MANUAL) is True:
            await self._clear_private_manual("timeout")

    async def _clear_private_manual(self, reason: str) -> None:
        ent = self._entity_id(CONF_PRIVATE_MANUAL)
        if not ent:
            return
        _LOGGER.info("media_apply: FLEET-98 private_time-Latch auto-clear (%s) → %s", reason, ent)
        await self._svc("homeassistant", "turn_off", {"entity_id": ent})

    async def _execute_tv_wol(self) -> None:
        """R12: TV einschalten. `media_player.turn_on` löst das webOS-„Leuchtfeuer"
        aus (bleibt 24/7); ist zusätzlich eine MAC konfiguriert, sendet media_apply
        das Magic-Packet selbst (variabel pflegbar)."""
        tv = self._entity_id(CONF_TV_PLAYER)
        if tv:
            await self._svc("media_player", "turn_on", {"entity_id": tv})
        mac = str(self._opts.get(CONF_TV_WOL_MAC, DEFAULT_TV_WOL_MAC) or "").strip()
        if mac:
            await self._svc("wake_on_lan", "send_magic_packet", {"mac": mac})
        _LOGGER.info("media_apply: R12 TV-WoL → turn_on %s (mac=%s)", tv, mac or "—")

    # ----- Wake-Sequenz (R23) + bio-Flanken -----
    def _bio_edges(self) -> tuple[bool, bool]:
        """bio_state-Flanken (to_awake, to_sleep) aus core_state. EINMAL pro Tick
        (mutiert Vortick-State); Erststand zählt nicht. to_awake = Eintritt in
        awake/waking (Wake, R23); to_sleep = Eintritt in sleep (FLEET-98 private-Clear)."""
        cur = self._state(CONF_BIO_STATE)
        prev = self._last_bio_state
        self._last_bio_state = cur
        to_awake = prev is not None and prev not in BIO_AWAKE_VALUES and cur in BIO_AWAKE_VALUES
        to_sleep = prev is not None and prev != BIO_SLEEP_VALUE and cur == BIO_SLEEP_VALUE
        return to_awake, to_sleep

    def _wake_trigger_fired(self, bio_to_awake: bool) -> bool:
        """Wake-Flanke = bio→awake (primär) ODER steigende Flanke eines optionalen
        Roh-Triggers (Multi-Entity, Default leer). Nur EINMAL pro Tick aufrufen."""
        fired = bio_to_awake
        ents = self._entity_id(CONF_WAKE_TRIGGERS)
        if isinstance(ents, str):
            ents = [ents]
        if isinstance(ents, (list, tuple)):
            for eid in ents:
                if not isinstance(eid, str) or not eid:
                    continue
                st = self.hass.states.get(eid)
                raw = st.state if st and st.state not in ("unknown", "unavailable") else None
                cur = _bool(raw)
                if self._last_wake_states.get(eid) is False and cur is True:
                    fired = True
                self._last_wake_states[eid] = cur
        return fired

    @callback
    def _schedule_wake(self) -> None:
        self._cancel_wake()
        self._wake_task = self.hass.async_create_task(self._run_wake())

    @callback
    def _cancel_wake(self) -> None:
        if self._wake_task is not None and not self._wake_task.done():
            self._wake_task.cancel()
        self._wake_task = None

    async def _run_wake(self) -> None:
        """R23: HomePods auf Startlautstärke → Debounce → Ramp auf das aktuelle
        media_policy-Ziel (`volume_target_homepods`). Abbrechbar; nutzt die normale
        Ramp-Maschine für den Hochlauf."""
        hp = self._entity_id(CONF_HOMEPODS_PLAYER)
        if not hp:
            return
        s = self.settings()
        start = round(max(0.0, min(1.0, s.wake_start_volume)), 3)
        try:
            self._cancel_ramp()
            # Race-Fix: Volume-Floor BLOCKIEREND setzen, damit er anliegt, bevor der
            # (auf derselben Wake-Flanke gestartete) Radio-Autostart Ton ausgibt.
            await self._svc(
                "media_player", "volume_set",
                {"entity_id": hp, "volume_level": start}, blocking=True,
            )
            await asyncio.sleep(max(0.0, s.wake_debounce_seconds))
            if logic.media_block_reason(self._build_inputs()):
                return
            target = self._float(CONF_VOL_TARGET_HOMEPODS)
            if target is None:
                return
            levels = logic.ramp_levels(start, target, s.ramp_steps, s.tiny_delta)
            if levels:
                self._cancel_ramp()
                self._ramp_task = self.hass.async_create_task(
                    self._run_ramp(hp, levels, s.ramp_step_delay_s)
                )
            _LOGGER.info("media_apply: R23 Wake-Sequenz %s → %.2f → Ramp auf %.2f", hp, start, target)
        except asyncio.CancelledError:
            raise
        finally:
            self._wake_task = None

    # ----- Sleep-TV-Off (R24) -----
    def _consume_extend_edge(self) -> bool:
        """Flanke: hat sich der Lichtschalter-Taster-State seit dem letzten Tick
        geändert? (Druck = State-Change). Nur EINMAL pro Tick aufrufen (mutiert)."""
        cur = self._state(CONF_SLEEP_TV_EXTEND)
        pressed = (
            self._last_extend_state is not None
            and cur is not None
            and cur != self._last_extend_state
        )
        self._last_extend_state = cur
        return pressed

    @callback
    def _schedule_sleep_tv(self) -> None:
        self._cancel_sleep_tv()
        self._sleep_tv_task = self.hass.async_create_task(self._run_sleep_tv())

    @callback
    def _cancel_sleep_tv(self) -> None:
        if self._sleep_tv_task is not None and not self._sleep_tv_task.done():
            self._sleep_tv_task.cancel()
        self._sleep_tv_task = None

    async def _run_sleep_tv(self) -> None:
        """R24: nach delay − warn_lead die Warnung, dann warn_lead später den TV aus
        (beides apply-gated). Extend startet den Task neu (voller delay); Cancel
        bricht ab. Abbrechbar via asyncio.CancelledError."""
        delay = self._duration(CONF_SLEEP_TV_OFF_DELAY, DEFAULT_SLEEP_TV_OFF_DELAY)
        lead = min(self._duration(CONF_SLEEP_TV_WARN_LEAD, DEFAULT_SLEEP_TV_WARN_LEAD), delay)
        try:
            await asyncio.sleep(max(0.0, delay - lead))
            if self.apply_enabled:
                await self._sleep_tv_warn()
            await asyncio.sleep(max(0.0, lead))
        except asyncio.CancelledError:
            raise
        self._sleep_tv_task = None
        self._sleep_tv_state.armed = False
        if self.apply_enabled:
            tv = self._entity_id(CONF_TV_PLAYER)
            if tv:
                _LOGGER.info("media_apply: R24 Sleep-TV-Off abgelaufen → turn_off %s", tv)
                await self._svc("media_player", "turn_off", {"entity_id": tv})
        else:
            _LOGGER.debug("media_apply: R24 Sleep-TV-Off abgelaufen (Shadow → kein Off)")

    async def _sleep_tv_warn(self) -> None:
        """TV-Warnung via konfiguriertem notify-Service (z.B. notify.living_lgtv).
        Leer/ohne Punkt → keine Warnung (degraded, schaltet trotzdem aus)."""
        svc = str(self._opts.get(CONF_SLEEP_TV_NOTIFY, DEFAULT_SLEEP_TV_NOTIFY) or "").strip()
        if "." not in svc:
            return
        domain, service = svc.split(".", 1)
        msg = self._opts.get(CONF_SLEEP_TV_WARN_MESSAGE) or DEFAULT_SLEEP_TV_WARN_MESSAGE
        await self._svc(domain, service, {"message": msg})

    # ----- Denon-Nachlauf (R13/R14) -----
    def _duration(self, key: str, default: float) -> float:
        try:
            return float(self._opts.get(key, default))
        except (TypeError, ValueError):
            return default

    @callback
    def _apply_nachlauf(self, nplan: "logic.NachlaufPlan") -> None:
        self._dispatch_timer("pc", nplan.pc, CONF_DENON_NACHLAUF_PC, DEFAULT_DENON_NACHLAUF_PC)
        self._dispatch_timer("tv", nplan.tv, CONF_DENON_NACHLAUF_TV, DEFAULT_DENON_NACHLAUF_TV)

    @callback
    def _dispatch_timer(self, key: str, intent: str, conf: str, default: float) -> None:
        if intent == logic.TIMER_ARM:
            self._schedule_nachlauf(key, self._duration(conf, default))
        elif intent in (logic.TIMER_CANCEL, logic.TIMER_PAUSE):
            # PAUSE bricht nur den realen Countdown ab; das armed/paused-Buchwerk
            # hält die Pure-Logic (Resume = Neustart nach Sleep-Ende).
            self._cancel_nachlauf(key)

    @callback
    def _schedule_nachlauf(self, key: str, duration: float) -> None:
        self._cancel_nachlauf(key)
        self._nachlauf_tasks[key] = self.hass.async_create_task(
            self._run_nachlauf(key, duration)
        )

    @callback
    def _cancel_nachlauf(self, key: str) -> None:
        task = self._nachlauf_tasks.pop(key, None)
        if task is not None and not task.done():
            task.cancel()

    async def _run_nachlauf(self, key: str, duration: float) -> None:
        """Wartet `duration` Sekunden, dann (gegatet) Denon aus. Abbrechbar:
        PC/TV zurück oder Sleep (R14) canceln den Task vorher."""
        try:
            await asyncio.sleep(max(0.0, duration))
        except asyncio.CancelledError:
            raise
        self._nachlauf_tasks.pop(key, None)
        # Armed-Flag proaktiv löschen (self-heal vor dem nächsten Tick).
        if key == "pc":
            self._nachlauf_state.pc_armed = False
        else:
            self._nachlauf_state.tv_armed = False
            self._nachlauf_state.tv_paused = False
        if self.apply_enabled:
            # FLEET-80: finaler Konsumenten-Check am Ablauf (event-getriebener
            # Cancel sollte schon gegriffen haben — doppelt safe gegen Races).
            if self._denon_consumer_active() is True:
                _LOGGER.info(
                    "media_apply: Nachlauf %s abgelaufen, aber Denon-Konsument "
                    "aktiv (media_device) → kein Off", key
                )
            else:
                await self._denon_power_off(key)
        else:
            _LOGGER.debug(
                "media_apply: Nachlauf %s abgelaufen (Shadow → kein Denon-Off)", key
            )
        if self.data is not None:
            self.async_set_updated_data({
                **self.data,
                "denon_nachlauf_active": (
                    self._nachlauf_state.pc_armed or self._nachlauf_state.tv_armed
                ),
            })

    async def _denon_power_off(self, key: str) -> None:
        denon = self._entity_id(CONF_DENON_PLAYER)
        if not denon:
            return
        _LOGGER.info("media_apply: Denon-Nachlauf %s abgelaufen → turn_off %s", key, denon)
        await self._svc("media_player", "turn_off", {"entity_id": denon})

    # ----- Radio-Shortcuts (manuell, Phase 4b) -----
    def _ma_config_entry_id(self) -> Optional[str]:
        """Config-Entry der Music-Assistant-Integration (für den Search-Service)."""
        for entry in self.hass.config_entries.async_entries("music_assistant"):
            return entry.entry_id
        return None

    async def async_play_radio(self, media_id: str) -> dict[str, Any]:
        """MANUELL einen Sender abspielen (Cockpit-Shortcut / Suchtreffer).

        Bewusster User-Befehl → spielt SOFORT, **unabhängig vom Shadow-Gate**
        (`apply_enabled`); nur der automatische Policy-Apply ist shadow-gated.
        `media_id` ist eine MA-URI (radiobrowser://, library://, …)."""
        hp = self._entity_id(CONF_HOMEPODS_PLAYER)
        if not hp:
            raise ValueError("HomePods-Player nicht gebunden")
        if not media_id:
            raise ValueError("media_id fehlt")
        await self._svc(
            "music_assistant", "play_media",
            {
                "entity_id": hp, "media_id": media_id,
                "media_type": RADIO_MEDIA_TYPE, "enqueue": RADIO_ENQUEUE,
            },
        )
        self.hass.async_create_task(self._radio_play_after(hp))
        return {"played": media_id, "target": hp}

    async def async_search_radio(self, query: str, limit: int | None = None) -> list[dict[str, Any]]:
        """Radiosender über Music Assistant suchen → normalisierte Trefferliste
        [{name, uri, image, favorite}]. Leere/keine Treffer → []."""
        query = (query or "").strip()
        if not query:
            return []
        entry_id = self._ma_config_entry_id()
        if not entry_id:
            raise ValueError("music_assistant nicht geladen")
        lim = int(limit or DEFAULT_RADIO_SEARCH_LIMIT)
        try:
            resp = await self.hass.services.async_call(
                "music_assistant", "search",
                {"config_entry_id": entry_id, "name": query,
                 "media_type": ["radio"], "limit": lim},
                blocking=True, return_response=True,
            )
        except Exception as err:  # noqa: BLE001 — Suche darf das Cockpit nicht crashen
            _LOGGER.warning("media_apply: radio search '%s' failed: %s", query, err)
            return []
        radio = (resp or {}).get("radio") or []
        out: list[dict[str, Any]] = []
        for item in radio:
            if not isinstance(item, dict) or not item.get("uri"):
                continue
            out.append({
                "name": item.get("name") or item["uri"],
                "uri": item["uri"],
                "image": item.get("image"),
                "favorite": bool(item.get("favorite")),
            })
        return out

    # ----- service surface -----
    async def async_set_apply_enabled(self, value: bool) -> None:
        """Apply zur Laufzeit an/aus. Schreibt in die Options → Reload-Listener."""
        new_options = {**self.entry.options, CONF_APPLY_ENABLED: bool(value)}
        self.hass.config_entries.async_update_entry(self.entry, options=new_options)
