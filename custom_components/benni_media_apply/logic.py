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
from typing import Any, Final, Optional

from .const import (
    ACTION_NONE,
    ACTION_PAUSE,
    ACTION_RESUME,
    ACTION_START_RADIO,
    DEFAULT_DEBOUNCE_SECONDS,
    DEFAULT_DUCKED_LEVEL,
    DEFAULT_RAMP_STEP_DELAY,
    DEFAULT_RAMP_STEPS,
    DEFAULT_TINY_DELTA,
    DEFAULT_WAKE_DEBOUNCE,
    DEFAULT_WAKE_START_VOLUME,
    EXEC_DEBOUNCE,
    EXEC_IMMEDIATE,
    EXEC_SHADOW,
    PLAYER_ADDRESSABLE_VALUES,
    PLAYER_OFF_VALUES,
    PLAYER_PLAYING_VALUES,
    RADIO_CATALOG,
    RADIO_STATION_LABELS,
    SCREEN_DEVICES,
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
    # Radio (Phase 4b). None = ungebunden/unbekannt ⇒ non-regressiv (erlauben).
    radio_station: Optional[str] = None
    radio_ready: Optional[bool] = None
    manual_playback: Optional[bool] = None
    planned_station_playing: Optional[bool] = None   # FLEET-79 Autostart-Gate
    # aktueller Geräte-Zustand (Ist, für Idempotenz):
    homepods_configured: bool = False
    homepods_state: Optional[str] = None
    homepods_volume: Optional[float] = None
    denon_configured: bool = False
    denon_state: Optional[str] = None
    denon_volume: Optional[float] = None
    subwoofer_configured: bool = False
    subwoofer_state: Optional[str] = None   # "on"/"off"/None
    # Phase 3 (R13/R14 Denon-Nachlauf). None = unbekannt/nicht gebunden ⇒ kein Arm.
    pc_power_on: Optional[bool] = None
    tv_power_on: Optional[bool] = None
    denon_power_on: Optional[bool] = None
    bio_sleep: Optional[bool] = None
    # Phase 4c (R12 TV-WoL). media_device = aktives Output-Gerät (media_state);
    # tv_player_state = WebOS-State (R11 primär). None = ungebunden/unbekannt.
    media_device: Optional[str] = None
    tv_player_state: Optional[str] = None
    # Phase 3b (R24 Sleep-TV-Off). Flanke vom Coordinator (Lichtschalter-Druck).
    sleep_tv_extend_pressed: bool = False
    # R23 (Wake-Sequenz). Flanke vom Coordinator (ein Wake-Trigger ging an).
    wake_trigger_fired: bool = False


@dataclass(frozen=True)
class RampSettings:
    ramp_steps: int = DEFAULT_RAMP_STEPS
    ramp_step_delay_s: float = DEFAULT_RAMP_STEP_DELAY
    tiny_delta: float = DEFAULT_TINY_DELTA
    ducked_level: float = DEFAULT_DUCKED_LEVEL
    debounce_seconds: float = DEFAULT_DEBOUNCE_SECONDS  # R2-Fenster (Coordinator-Timing)
    wake_start_volume: float = DEFAULT_WAKE_START_VOLUME  # R23 HomePods-Startlautstärke
    wake_debounce_seconds: float = DEFAULT_WAKE_DEBOUNCE


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
    radio_uri: Optional[str] = None        # aufgelöster Sender-URI (start_radio inline)
    reasons: list = field(default_factory=list)

    @property
    def has_work(self) -> bool:
        """True, wenn der Plan tatsächlich etwas am Gerät tut. Triviale Pläne
        (nur Re-Eval ohne Soll≠Ist) dürfen ein laufendes Debounce-Fenster NICHT
        neu starten — sonst hungert ein gepufferter echter Plan aus."""
        return bool(
            self.homepods_action != ACTION_NONE
            or self.homepods_levels
            or self.denon_set is not None
            or self.subwoofer_set is not None
        )

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
            "radio_uri": self.radio_uri,
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


def resolve_radio_uri(station: Optional[str]) -> Optional[str]:
    """Sender-Key → radiobrowser-URI (Phase 4b Katalog-Port). None bei
    ungebundenem/unbekanntem Sender ⇒ Coordinator fällt auf das YAML-Script zurück."""
    if not station:
        return None
    return RADIO_CATALOG.get(station)


def should_autostart_radio(inp: "Inputs") -> bool:
    """FLEET-79: Gate für den Radio-Autostart (Wake / Resume). Nur wenn ein gültiger
    Sender bereit ist (`radio_ready` True), KEINE manuelle Wiedergabe läuft und die
    geplante Station NICHT eh schon spielt. Der Trigger (Wake-Flanke / manual-off-
    Flanke) sowie das Latch-Lösen liegen im Coordinator. None (ungebunden) = blockt
    (radio_ready muss explizit True sein → kein Autostart ohne validen Sender)."""
    return (
        inp.radio_ready is True
        and inp.manual_playback is not True
        and inp.planned_station_playing is not True
    )


def radio_defaults() -> list[dict[str, str]]:
    """Default-Sender als Shortcut-Liste fürs Cockpit: [{key, name, uri}].
    Name aus RADIO_STATION_LABELS (Fallback: Key), sortiert nach Anzeigenamen."""
    out = [
        {"key": key, "name": RADIO_STATION_LABELS.get(key, key), "uri": uri}
        for key, uri in RADIO_CATALOG.items()
    ]
    return sorted(out, key=lambda s: s["name"].lower())


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
# Ausführungs-Modus (R2 Debounce / R3 Queue-statt-Race)
# --------------------------------------------------------------------------- #
def execution_mode(plan: "ApplyPlan") -> str:
    """Entscheidet, WIE der berechnete Plan zum Gerät kommt (Pure-Teil von R2/R3).

    - ``EXEC_SHADOW``: ``apply_enabled`` aus → gar nicht ausführen (nur Preview).
    - ``EXEC_IMMEDIATE``: Quiet-Mode bricht sofort durch — kein Debounce, der
      laufende Ramp wird abgebrochen (R2/R3-Ausnahme).
    - ``EXEC_DEBOUNCE``: Normalfall — Ausführung wartet das R2-Fenster ab, sodass
      ein Trigger-Burst zu EINER konsolidierten Aktion zusammenfällt.

    Das reale Timing/Serialisieren liegt im Coordinator; hier wohnt nur die
    HA-freie Klassifikation (testbar)."""
    if not plan.execute:
        return EXEC_SHADOW
    if plan.quiet_override:
        return EXEC_IMMEDIATE
    return EXEC_DEBOUNCE


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
    elif (
        action == ACTION_START_RADIO
        and not hp_playing
        and not inp.stop_latch
        # Radio-Gates wie im YAML-Script (None = ungebunden ⇒ non-regressiv erlauben).
        and inp.radio_ready is not False
        and inp.manual_playback is not True
    ):
        p.homepods_action = ACTION_START_RADIO
        p.radio_uri = resolve_radio_uri(inp.radio_station)
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


# --------------------------------------------------------------------------- #
# Phase 3 — Denon-Nachlauf (R13/R14)
# --------------------------------------------------------------------------- #
# Timer-Intents: der Coordinator besitzt den realen asyncio-Countdown, die
# Pure-Logic entscheidet nur die Flanke (arm/cancel/pause) und führt das
# Armed-Buchwerk über die Ticks. Expiry-Aktion ist fix: Denon ausschalten.
TIMER_NONE: Final = "none"
TIMER_ARM: Final = "arm"
TIMER_CANCEL: Final = "cancel"
TIMER_PAUSE: Final = "pause"


@dataclass
class NachlaufState:
    """Armed-Buchwerk der Nachlauf-Timer zwischen Coordinator-Ticks (RAM)."""

    pc_armed: bool = False
    tv_armed: bool = False
    tv_paused: bool = False   # R14: während Sleep pausiert (nicht abgebrochen)
    # FLEET-80: Vortick-Power für KANTEN-getriggertes Armen (sonst Dauer-Loop:
    # PC/TV im Steady-State aus → arm → 90s → Denon aus → re-arm …). None = unbekannt.
    last_pc_on: Optional[bool] = None
    last_tv_on: Optional[bool] = None


@dataclass
class NachlaufPlan:
    """Flanken-Intent pro Timer. NONE = unverändert lassen."""

    pc: str = TIMER_NONE
    tv: str = TIMER_NONE
    reasons: list = field(default_factory=list)

    @property
    def active(self) -> bool:
        return self.pc != TIMER_NONE or self.tv != TIMER_NONE

    def as_dict(self) -> dict[str, Any]:
        return {"pc": self.pc, "tv": self.tv, "reasons": list(self.reasons)}


def decide_denon_nachlauf(
    inp: Inputs, state: Optional[NachlaufState] = None
) -> tuple[NachlaufPlan, NachlaufState]:
    """R13/R14: Denon-Nachlauf nach PC-/TV-Aus.

    R13 (PC): PC aus + Denon noch an → 90s-Timer. PC zurück (oder Denon schon
              aus / Daten unbekannt) → abbrechen. Expiry → Denon aus.
    R14 (TV): wie R13, aber **Sleep pausiert** den Timer (nicht abbrechen):
              während bio_sleep wird ein laufender Timer ausgesetzt und nach
              Sleep-Ende — falls TV weiter aus & Denon an — neu gestartet.

    Arm-Bedingung verlangt EXPLIZIT power_on==False & denon_power_on==True;
    None (unbekannt/ungebunden) armt nie und bricht einen laufenden Timer ab
    (kein Off-Schalten auf Basis fehlender Daten)."""
    if state is None:
        state = NachlaufState()
    p = NachlaufPlan()
    ns = NachlaufState(
        pc_armed=state.pc_armed, tv_armed=state.tv_armed, tv_paused=state.tv_paused,
        last_pc_on=state.last_pc_on, last_tv_on=state.last_tv_on,
    )
    reasons: list[str] = []
    denon_on = inp.denon_power_on is True

    # ----- R13: PC-Aus (KANTEN-getriggert, FLEET-80) -----
    # Armen NUR auf der Fallflanke PC an→aus bei laufendem Denon. Steady-State
    # „PC aus" (Normalfall beim TV-Schauen) darf NICHT (re-)armen → kein 90s-Loop.
    pc_off_edge = state.last_pc_on is True and inp.pc_power_on is False
    pc_hold = inp.pc_power_on is False and denon_on   # hält den laufenden Timer
    if pc_off_edge and denon_on and not ns.pc_armed:
        p.pc = TIMER_ARM
        ns.pc_armed = True
        reasons.append("r13:arm_pc")
    elif ns.pc_armed and not pc_hold:
        # PC zurück ODER Denon aus → Timer abbrechen.
        p.pc = TIMER_CANCEL
        ns.pc_armed = False
        reasons.append("r13:cancel_pc")

    # ----- R14: TV-Aus (Sleep pausiert; KANTEN-getriggert) -----
    tv_off_edge = state.last_tv_on is True and inp.tv_power_on is False
    tv_hold = inp.tv_power_on is False and denon_on
    if inp.bio_sleep is True:
        if ns.tv_armed and not ns.tv_paused:
            p.tv = TIMER_PAUSE
            ns.tv_paused = True
            reasons.append("r14:pause_sleep")
    else:
        if tv_off_edge and denon_on and not ns.tv_armed:
            p.tv = TIMER_ARM
            ns.tv_armed = True
            ns.tv_paused = False
            reasons.append("r14:arm_tv")
        elif ns.tv_armed and ns.tv_paused and tv_hold:
            # Sleep-Ende, Bedingung hält → Timer neu starten (Resume, keine Flanke nötig).
            p.tv = TIMER_ARM
            ns.tv_paused = False
            reasons.append("r14:resume_tv")
        elif ns.tv_armed and not tv_hold:
            p.tv = TIMER_CANCEL
            ns.tv_armed = False
            ns.tv_paused = False
            reasons.append("r14:cancel_tv")

    # Vortick-Power fortschreiben (Flankenerkennung im nächsten Tick).
    ns.last_pc_on = inp.pc_power_on
    ns.last_tv_on = inp.tv_power_on
    p.reasons = reasons
    return p, ns


# --------------------------------------------------------------------------- #
# Phase 4c — TV-WoL (R12): Bildschirm-Szenario → TV einschalten (ohne Debounce)
# --------------------------------------------------------------------------- #
@dataclass
class TvWolState:
    """Edge-Buchwerk: True, sobald für die laufende Bildschirm-Episode der TV-On
    schon ausgelöst wurde (verhindert WoL-Spam, bis TV an ODER Szenario verlässt)."""

    fired: bool = False


@dataclass
class TvWolPlan:
    fire: bool = False
    reasons: list = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"fire": self.fire, "reasons": list(self.reasons)}


def _tv_is_off(inp: "Inputs") -> Optional[bool]:
    """R11: TV-Power. WebOS-State primär (off/standby = aus, sonst an); ist der
    Player ungebunden/unbekannt → Wattage-Fallback (tv_power_on). None = unbekannt."""
    st = inp.tv_player_state
    if st is not None and st not in ("unknown", "unavailable"):
        return st in PLAYER_OFF_VALUES
    if inp.tv_power_on is None:
        return None
    return not inp.tv_power_on


def decide_tv_wol(
    inp: "Inputs", state: Optional[TvWolState] = None
) -> tuple[TvWolPlan, TvWolState]:
    """R12 — Wechsel auf ein Bildschirm-Szenario (media_device ∈ SCREEN_DEVICES)
    bei ausgeschaltetem TV → TV einschalten (sofort, kein Debounce). Edge-getriggert:
    feuert genau EINMAL pro Episode; Reset, sobald TV an ist ODER das Szenario kein
    Bildschirm mehr verlangt. Unbekannter TV-Zustand (None) feuert NICHT (fail-safe)."""
    if state is None:
        state = TvWolState()
    p = TvWolPlan()
    ns = TvWolState(fired=state.fired)
    reasons: list[str] = []

    screen = inp.media_device in SCREEN_DEVICES
    tv_off = _tv_is_off(inp)

    if not screen or tv_off is False:
        # Kein Bildschirm-Szenario oder TV ist an → Episode beendet, re-armen.
        if ns.fired:
            reasons.append("r12:reset")
        ns.fired = False
    elif screen and tv_off is True and not ns.fired:
        p.fire = True
        ns.fired = True
        reasons.append("r12:tv_on")
    # screen & tv_off is None → unbekannt, nichts tun (fail-safe).

    p.reasons = reasons
    return p, ns


# --------------------------------------------------------------------------- #
# Phase 3b — Sleep-TV-Off (R24): Sleep + TV läuft → 45 min → Warnung → TV aus
# --------------------------------------------------------------------------- #
@dataclass
class SleepTvState:
    """Edge-Buchwerk des Sleep-TV-Off-Timers (RAM/Coordinator-Ticks)."""

    armed: bool = False


@dataclass
class SleepTvPlan:
    """Flanken-Intent (ARM/CANCEL/EXTEND/NONE) — der Coordinator besitzt den Timer."""

    intent: str = TIMER_NONE
    reasons: list = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"intent": self.intent, "reasons": list(self.reasons)}


TIMER_EXTEND: Final = "extend"


def decide_sleep_tv(
    inp: "Inputs", state: Optional[SleepTvState] = None
) -> tuple[SleepTvPlan, SleepTvState]:
    """R24: Bio-State=sleep + TV läuft → Timer arm (Coordinator: 45 min → Warnung
    → TV aus). Lichtschalter-Druck verlängert (EXTEND). Sleep-Ende oder TV aus →
    CANCEL. TV-Zustand unbekannt (None) armt nicht (fail-safe)."""
    if state is None:
        state = SleepTvState()
    p = SleepTvPlan()
    ns = SleepTvState(armed=state.armed)
    reasons: list[str] = []

    tv_off = _tv_is_off(inp)
    cond = inp.bio_sleep is True and tv_off is False   # Sleep aktiv UND TV an

    if cond and not ns.armed:
        p.intent = TIMER_ARM
        ns.armed = True
        reasons.append("r24:arm")
    elif cond and ns.armed and inp.sleep_tv_extend_pressed:
        p.intent = TIMER_EXTEND
        reasons.append("r24:extend")
    elif not cond and ns.armed:
        p.intent = TIMER_CANCEL
        ns.armed = False
        reasons.append("r24:cancel")

    p.reasons = reasons
    return p, ns


# --------------------------------------------------------------------------- #
# Phase R23 — Wake-Sequenz: Trigger-Flanke → HomePods 0.10 → Ramp auf Ziel
# --------------------------------------------------------------------------- #
@dataclass
class WakePlan:
    fire: bool = False
    reasons: list = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"fire": self.fire, "reasons": list(self.reasons)}


def decide_wake(inp: "Inputs") -> WakePlan:
    """R23: Eine steigende Flanke eines Wake-Triggers (Kaffeemaschine, Fenster,
    PS5-/PC-Ein, Private-Time) startet die Wake-Sequenz — der Coordinator setzt
    HomePods auf die Startlautstärke und rampt nach dem Debounce auf das
    media_policy-Ziel. Im Sleep unterdrückt (R25 dominant); `waking`/`awake`
    (= nicht sleep) sind erlaubt (KH-4). Stateless: Flankenerkennung im Coordinator."""
    p = WakePlan()
    if not inp.wake_trigger_fired:
        return p
    if inp.bio_sleep is True:
        p.reasons.append("r23:suppressed_sleep")
        return p
    p.fire = True
    p.reasons.append("r23:wake")
    return p
