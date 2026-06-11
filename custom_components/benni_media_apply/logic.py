"""HA-freie Apply-Engine für benni_media_apply (Executor).

Rechnet NICHTS neu — nimmt die Targets/Action aus media_policy und entscheidet,
WAS am Gerät zu tun ist: idempotent (nur bei Ist≠Soll) und geramped (HomePods
16×1s, Tiny-Delta direkt; Denon hart). Quiet → direkt (kein Ramp). Apply-Gate:
`apply_enabled` (global, Shadow) × `volume_apply_allowed` (pro Entscheidung).

Keine HA-Imports. Der Coordinator macht das Entity-State-Plumbing, führt die
Ramp-Sequenz als (abbrechbaren) Task aus und ruft die Services.

Phase 1 (FLEET-40): Volume (Ramp/direct), HomePods-Action (pause/play;
start_radio delegiert der Coordinator an ein Script), Subwoofer on/off.
Restore (R20), Denon-Nachlauf (R13/R14), Sleep-Off (R24/R25) folgen.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .const import (
    ACTION_NONE,
    ACTION_PAUSE,
    ACTION_RESUME,
    ACTION_START_RADIO,
    DEFAULT_DUCKED_LEVEL,
    DEFAULT_RAMP_STEP_DELAY,
    DEFAULT_RAMP_STEPS,
    DEFAULT_TINY_DELTA,
    PLAYER_ADDRESSABLE_VALUES,
    PLAYER_PLAYING_VALUES,
)


# --------------------------------------------------------------------------- #
# Inputs / Settings / Plan
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Inputs:
    """Snapshot der Apply-Eingänge. None = unknown/nicht gebunden."""

    apply_enabled: bool = False           # globaler Shadow-Kill-Switch (Option)
    # aus media_policy:
    volume_apply_allowed: bool = False
    action: str = ACTION_NONE
    homepods_should_pause: bool = False
    homepods_resume_allowed: bool = False
    homepods_target: Optional[float] = None
    denon_target: Optional[float] = None
    subwoofer_allowed: bool = False
    # aus media_state:
    quiet_mode: bool = False
    stop_latch: bool = False
    # aktueller Geräte-Zustand (Ist, für Idempotenz):
    homepods_configured: bool = False
    homepods_state: Optional[str] = None
    homepods_volume: Optional[float] = None
    denon_configured: bool = False
    denon_state: Optional[str] = None
    denon_volume: Optional[float] = None
    subwoofer_configured: bool = False
    subwoofer_state: Optional[str] = None   # "on"/"off"/None


@dataclass(frozen=True)
class RampSettings:
    ramp_steps: int = DEFAULT_RAMP_STEPS
    ramp_step_delay_s: float = DEFAULT_RAMP_STEP_DELAY
    tiny_delta: float = DEFAULT_TINY_DELTA
    ducked_level: float = DEFAULT_DUCKED_LEVEL


@dataclass
class ApplyState:
    """Persistenter Zustand zwischen Coordinator-Ticks (RAM). Trägt den
    R20-Pre-Quiet-Snapshot + die Quiet-Edge-Buchführung."""

    was_quiet: bool = False
    pre_quiet_homepods: Optional[float] = None   # Pre-Quiet-Target (Snapshot, R20)
    pre_quiet_denon: Optional[float] = None
    last_homepods_target: Optional[float] = None  # Vortick-Target (Quelle des Snapshots)
    last_denon_target: Optional[float] = None


@dataclass
class ApplyPlan:
    """Was der Coordinator tun soll. Im Shadow (execute=False) nur Debug."""

    execute: bool = False                  # apply_enabled (globaler Gate)
    homepods_action: str = ACTION_NONE     # pause/play/start_radio/none
    homepods_levels: list = field(default_factory=list)  # Volume-Set-Sequenz
    homepods_ramp: bool = False            # True = gestuft (Ramp-Task), False = direkt
    denon_set: Optional[float] = None      # harter Set-Wert (None = no-op)
    subwoofer_set: Optional[bool] = None   # True/False/None (None = no-op)
    quiet_override: bool = False           # Quiet → direkt, laufenden Ramp abbrechen
    is_restore: bool = False               # R20: Quiet-Ende → Ramp-Up auf Pre-Quiet
    reasons: list = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "execute": self.execute,
            "homepods_action": self.homepods_action,
            "homepods_target": self.homepods_levels[-1] if self.homepods_levels else None,
            "homepods_ramp": self.homepods_ramp,
            "denon_target": self.denon_set,
            "subwoofer_set": self.subwoofer_set,
            "quiet_override": self.quiet_override,
            "is_restore": self.is_restore,
            "reasons": list(self.reasons),
        }


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _eq(a: float, b: float) -> bool:
    return abs(a - b) < 1e-9


def ramp_levels(
    current: Optional[float], target: Optional[float], steps: int, tiny_delta: float
) -> list[float]:
    """Volume-Set-Sequenz von current → target.

    - target None         → [] (nichts zu tun)
    - current None        → [target] (kein Ist → direkt setzen)
    - |Δ| == 0            → [] (Ist == Soll, idempotenter No-op)
    - |Δ| <= tiny_delta   → [target] (Tiny-Delta → direkt, kein Ramp)
    - sonst               → `steps` Zwischenstufen, letzte == target
    """
    if target is None:
        return []
    t = round(_clamp(target, 0.0, 1.0), 3)
    if current is None:
        return [t]
    c = round(_clamp(current, 0.0, 1.0), 3)
    delta = t - c
    if _eq(delta, 0.0):
        return []
    if abs(delta) <= tiny_delta:
        return [t]
    n = max(1, int(steps))
    return [round(c + delta * i / n, 3) for i in range(1, n + 1)]


def _direct(current: Optional[float], target: Optional[float]) -> list[float]:
    """Einzelner, idempotenter Direkt-Set (kein Ramp). [] wenn Ist==Soll."""
    if target is None:
        return []
    t = round(_clamp(target, 0.0, 1.0), 3)
    if current is None:
        return [t]
    if _eq(t, round(_clamp(current, 0.0, 1.0), 3)):
        return []
    return [t]


# --------------------------------------------------------------------------- #
# Master-Entscheidung
# --------------------------------------------------------------------------- #
def decide_apply(
    inp: Inputs,
    state: Optional[ApplyState] = None,
    settings: Optional[RampSettings] = None,
) -> tuple[ApplyPlan, ApplyState]:
    """Berechnet (Apply-Plan, nächster Zustand). Seiteneffekt-frei; der
    Coordinator führt aus + hält den ApplyState über die Ticks."""
    if settings is None:
        settings = RampSettings()
    if state is None:
        state = ApplyState()
    p = ApplyPlan()
    p.execute = inp.apply_enabled
    reasons: list[str] = []

    # ----- Quiet-Edges + Pre-Quiet-Snapshot (R20) -----
    quiet_entry = inp.quiet_mode and not state.was_quiet
    quiet_exit = (not inp.quiet_mode) and state.was_quiet
    new_state = ApplyState(
        was_quiet=inp.quiet_mode,
        pre_quiet_homepods=state.pre_quiet_homepods,
        pre_quiet_denon=state.pre_quiet_denon,
        # Vortick-Target nur außerhalb von Quiet fortschreiben — während Quiet
        # bleibt der Pre-Quiet-Wert eingefroren (sonst ginge er auf 0.10 verloren).
        last_homepods_target=inp.homepods_target if not inp.quiet_mode else state.last_homepods_target,
        last_denon_target=inp.denon_target if not inp.quiet_mode else state.last_denon_target,
    )
    if quiet_entry:
        # Snapshot des Pre-Quiet-Targets (der Vortick-Wert, vor dem Ducking).
        new_state.pre_quiet_homepods = state.last_homepods_target
        new_state.pre_quiet_denon = state.last_denon_target

    # ----- HomePods-Action (geräte-zustands-idempotent) -----
    hp_playing = inp.homepods_state in PLAYER_PLAYING_VALUES
    action = inp.action or ACTION_NONE
    if action == ACTION_PAUSE and inp.homepods_should_pause and hp_playing:
        p.homepods_action = ACTION_PAUSE
        reasons.append("action:pause")
    elif (
        action == ACTION_RESUME
        and inp.homepods_resume_allowed
        and not hp_playing
        and not inp.stop_latch
    ):
        p.homepods_action = ACTION_RESUME
        reasons.append("action:resume")
    elif action == ACTION_START_RADIO and not hp_playing and not inp.stop_latch:
        p.homepods_action = ACTION_START_RADIO
        reasons.append("action:start_radio")
    else:
        p.homepods_action = ACTION_NONE

    # ----- Volume (nur wenn die Policy es erlaubt) -----
    if inp.volume_apply_allowed:
        p.quiet_override = inp.quiet_mode
        if quiet_exit and new_state.pre_quiet_homepods is not None:
            # R20: Quiet-Ende → Restore auf Pre-Quiet (HomePods rampen, Denon hart).
            if inp.homepods_configured and inp.homepods_state in PLAYER_ADDRESSABLE_VALUES:
                p.homepods_levels = ramp_levels(
                    inp.homepods_volume, new_state.pre_quiet_homepods,
                    settings.ramp_steps, settings.tiny_delta,
                )
                p.homepods_ramp = len(p.homepods_levels) > 1
                if p.homepods_levels:
                    p.is_restore = True
                    reasons.append("restore:r20_quiet_end")
            if (
                inp.denon_configured
                and new_state.pre_quiet_denon is not None
                and inp.denon_state in PLAYER_ADDRESSABLE_VALUES
            ):
                d = _direct(inp.denon_volume, new_state.pre_quiet_denon)
                p.denon_set = d[0] if d else None
                if p.denon_set is not None:
                    p.is_restore = True
                    reasons.append("restore:denon_hard")
        else:
            # ---- Phase-1-Normalfall ----
            if (
                inp.homepods_configured
                and inp.homepods_target is not None
                and inp.homepods_state in PLAYER_ADDRESSABLE_VALUES
            ):
                if inp.quiet_mode:
                    # R20: Quiet → hart/direkt (kein Ramp), laufenden Ramp abbrechen.
                    p.homepods_levels = _direct(inp.homepods_volume, inp.homepods_target)
                    p.homepods_ramp = False
                else:
                    p.homepods_levels = ramp_levels(
                        inp.homepods_volume, inp.homepods_target,
                        settings.ramp_steps, settings.tiny_delta,
                    )
                    p.homepods_ramp = len(p.homepods_levels) > 1
                if p.homepods_levels:
                    reasons.append("volume:homepods_ramp" if p.homepods_ramp else "volume:homepods_direct")
            # Denon: immer hart (kein Ramp), idempotent.
            if (
                inp.denon_configured
                and inp.denon_target is not None
                and inp.denon_state in PLAYER_ADDRESSABLE_VALUES
            ):
                denon = _direct(inp.denon_volume, inp.denon_target)
                p.denon_set = denon[0] if denon else None
                if p.denon_set is not None:
                    reasons.append("volume:denon_set")
    else:
        reasons.append("volume:not_allowed")

    # Snapshot nach dem Restore wieder freigeben.
    if quiet_exit:
        new_state.pre_quiet_homepods = None
        new_state.pre_quiet_denon = None

    # ----- Subwoofer (idempotent on/off) -----
    if inp.subwoofer_configured and inp.subwoofer_state in ("on", "off"):
        cur_on = inp.subwoofer_state == "on"
        if inp.subwoofer_allowed != cur_on:
            p.subwoofer_set = inp.subwoofer_allowed
            reasons.append("subwoofer:on" if inp.subwoofer_allowed else "subwoofer:off")

    if not p.execute:
        reasons.append("shadow:apply_disabled")
    p.reasons = reasons
    return p, new_state
