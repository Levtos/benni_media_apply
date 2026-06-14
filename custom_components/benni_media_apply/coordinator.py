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
from typing import Any, Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from . import logic
from .const import (
    ACTION_PAUSE,
    ACTION_RESUME,
    ACTION_START_RADIO,
    BIO_SLEEP_VALUE,
    CONF_ACTION,
    CONF_APPLY_ENABLED,
    CONF_AUDIO_OWNER,
    CONF_BIO_STATE,
    CONF_DENON_NACHLAUF_PC,
    CONF_DENON_NACHLAUF_TV,
    CONF_DENON_PLAYER,
    CONF_DENON_POWER,
    CONF_DUCKED_LEVEL,
    CONF_HOMEPODS_PLAYER,
    CONF_HOMEPODS_RESUME_ALLOWED,
    CONF_HOMEPODS_SHOULD_PAUSE,
    CONF_PC_POWER,
    CONF_PROFILE,
    CONF_QUIET_MODE,
    CONF_RADIO_START_SCRIPT,
    CONF_RAMP_STEP_DELAY,
    CONF_RAMP_STEPS,
    CONF_STOP_LATCH,
    CONF_SUBWOOFER_ALLOWED,
    CONF_SUBWOOFER_SWITCH,
    CONF_TINY_DELTA,
    CONF_TV_POWER,
    CONF_VOL_TARGET_DENON,
    CONF_VOL_TARGET_HOMEPODS,
    CONF_VOLUME_APPLY_ALLOWED,
    CONF_VOLUME_POLICY,
    DEFAULT_APPLY_ENABLED,
    DEFAULT_DENON_NACHLAUF_PC,
    DEFAULT_DENON_NACHLAUF_TV,
    DEFAULT_DUCKED_LEVEL,
    DEFAULT_PROFILE,
    DEFAULT_RADIO_START_SCRIPT,
    DEFAULT_RAMP_STEP_DELAY,
    DEFAULT_RAMP_STEPS,
    DEFAULT_TINY_DELTA,
    DOMAIN,
    PLAYER_OFF_VALUES,
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
        self._apply_state = logic.ApplyState()
        self._nachlauf_state = logic.NachlaufState()
        self._nachlauf_tasks: dict[str, asyncio.Task] = {}
        self._last_debug: dict[str, Any] = {}

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
        """Unload-Hook: laufende Ramp- und Nachlauf-Tasks sauber abbrechen."""
        self._cancel_ramp()
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
            stop_latch=_bool(self._state(CONF_STOP_LATCH)),
            homepods_configured=bool(self._entity_id(CONF_HOMEPODS_PLAYER)),
            homepods_state=self._state(CONF_HOMEPODS_PLAYER),
            homepods_volume=self._attr_float(CONF_HOMEPODS_PLAYER, "volume_level"),
            denon_configured=bool(self._entity_id(CONF_DENON_PLAYER)),
            denon_state=self._state(CONF_DENON_PLAYER),
            denon_volume=self._attr_float(CONF_DENON_PLAYER, "volume_level"),
            subwoofer_configured=bool(self._entity_id(CONF_SUBWOOFER_SWITCH)),
            subwoofer_state=self._state(CONF_SUBWOOFER_SWITCH),
            # Phase 3 (R13/R14): None solange PC/TV-Power-Atomics ungebunden (#54).
            pc_power_on=self._tri_bool(CONF_PC_POWER),
            tv_power_on=self._tri_bool(CONF_TV_POWER),
            denon_power_on=self._denon_power_on(),
            bio_sleep=self._bio_sleep(),
        )

    def _compute(self) -> dict[str, Any]:
        inputs = self._build_inputs()
        plan, self._apply_state = logic.decide_apply(
            inputs, self._apply_state, self.settings()
        )
        nplan, self._nachlauf_state = logic.decide_denon_nachlauf(
            inputs, self._nachlauf_state
        )
        self._last_debug = {**plan.as_dict(), "nachlauf": nplan.as_dict()}
        if plan.execute:
            self.hass.async_create_task(self._execute(plan))
        # Nachlauf-Flanken IMMER verarbeiten (Arm/Cancel-Buchwerk auch im Shadow,
        # für Observability); der reale Denon-Off ist in _run_nachlauf gegatet.
        if nplan.active:
            self._apply_nachlauf(nplan)
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
            "plan": plan,
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
                "tasks": sorted(self._nachlauf_tasks),
            },
            "settings": {
                "ramp_steps": s.ramp_steps,
                "ramp_step_delay_s": s.ramp_step_delay_s,
                "tiny_delta": s.tiny_delta,
                "ducked_level": s.ducked_level,
            },
            "bindings": self.bindings(),
        }

    def debug(self) -> dict[str, Any]:
        return {
            **self._last_debug,
            "ramp_active": self._ramp_active,
            "nachlauf": {
                "pc_armed": self._nachlauf_state.pc_armed,
                "tv_armed": self._nachlauf_state.tv_armed,
                "tv_paused": self._nachlauf_state.tv_paused,
                "tasks": sorted(self._nachlauf_tasks),
            },
            "bindings": self.bindings(),
        }

    # ----- execution (side effects) -----
    async def _svc(self, domain: str, service: str, data: dict[str, Any]) -> None:
        try:
            await self.hass.services.async_call(domain, service, data, blocking=False)
        except Exception as err:  # noqa: BLE001 — Geräte-Fehler dürfen Apply nicht crashen.
            _LOGGER.warning("media_apply: %s.%s %s failed: %s", domain, service, data, err)

    async def _execute(self, plan: logic.ApplyPlan) -> None:
        hp = self._entity_id(CONF_HOMEPODS_PLAYER)
        denon = self._entity_id(CONF_DENON_PLAYER)
        sub = self._entity_id(CONF_SUBWOOFER_SWITCH)

        if plan.quiet_override:
            self._cancel_ramp()

        # ----- HomePods-Action -----
        if hp:
            if plan.homepods_action == ACTION_PAUSE:
                await self._svc("media_player", "media_pause", {"entity_id": hp})
            elif plan.homepods_action == ACTION_RESUME:
                await self._svc("media_player", "media_play", {"entity_id": hp})
            elif plan.homepods_action == ACTION_START_RADIO:
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
        if self.data is not None:
            self.async_set_updated_data({**self.data, "ramp_active": active})

    async def _run_ramp(self, entity_id: str, levels: list[float], delay: float) -> None:
        """HomePods-Volume-Ramp: Schritt für Schritt mit Delay, abbrechbar."""
        self._set_ramp_active(True)
        try:
            for lv in levels:
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

    # ----- service surface -----
    async def async_set_apply_enabled(self, value: bool) -> None:
        """Apply zur Laufzeit an/aus. Schreibt in die Options → Reload-Listener."""
        new_options = {**self.entry.options, CONF_APPLY_ENABLED: bool(value)}
        self.hass.config_entries.async_update_entry(self.entry, options=new_options)
