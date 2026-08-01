"""benni_media#13 — Übergangs-Latenz: HomePods-Rampe vs. Denon-Sofort-Apply.

Kernunterscheidung (Benni, fachliche Korrektur):

- **HomePods** können weich rampen. Die ~16 s Rampe ist GEWOLLT und bleibt —
  ebenso die sanfte R23-Wake-Sequenz. Sie darf nicht global geopfert werden.
- **Der Denon** kann technisch keine sinnvolle Rampe abbilden. Sein Ziel muss
  gesetzt werden, sobald Zielkontext und Zielwert feststehen.

Belegte Evidenz (Recorder, 2026-07-31, TV-Start):

    22:23:18.897  sensor.benni_master_denon      off → active
    22:23:39.860  sensor.benni_master_tv         off → active   (+20.96 s)
    22:23:43.864  media_context                  idle → tv      (+4.00 s)
    22:23:43.872  volume_target_denon            0.0 → 0.27     (+0.008 s)
    22:23:49.176  Denon steht real auf 27 %                      (+5.30 s)

Die letzten 5.3 s sind das R2-Debounce-Fenster vor einem EINZIGEN Service-Call
— genau das entfällt hier. Die 20.96 s davor liegen upstream (core_devices/Z2M),
die 4.00 s in media_state; beides ist NICHT Teil dieser Datei.
"""
from __future__ import annotations

import bma_const as C
import bma_logic as L


# --------------------------------------------------------------------------- #
# 1. HomePods: Rampe bleibt unangetastet
# --------------------------------------------------------------------------- #
def test_homepods_ramp_defaults_are_unchanged():
    """Die sanfte HomePods-Rampe (16 × 1.0 s) ist gewollt und bleibt."""
    assert C.DEFAULT_RAMP_STEPS == 16
    assert C.DEFAULT_RAMP_STEP_DELAY == 1.0
    s = L.RampSettings()
    assert s.ramp_steps == 16
    assert s.ramp_step_delay_s == 1.0


def test_homepods_ramp_still_produces_the_full_step_sequence():
    """Kein globales Verkürzen: 16 Stufen, letzte exakt auf dem Ziel."""
    levels = L.ramp_levels(0.10, 0.25, C.DEFAULT_RAMP_STEPS, C.DEFAULT_TINY_DELTA)
    assert len(levels) == 16
    assert levels[-1] == 0.25


def test_wake_start_volume_and_debounce_unchanged():
    """R23-Wake-Sequenz darf nicht regressieren."""
    s = L.RampSettings()
    assert s.wake_start_volume == C.DEFAULT_WAKE_START_VOLUME
    assert s.wake_debounce_seconds == C.DEFAULT_WAKE_DEBOUNCE


def _vol_inp(**kw):
    base = dict(
        apply_enabled=True,
        volume_apply_allowed=True,
        action=C.ACTION_NONE,
        homepods_configured=True,
        homepods_state="playing",
        homepods_volume=0.10,
        homepods_target=0.25,
        denon_configured=True,
        denon_state="on",
        denon_volume=0.0,
        denon_target=0.27,
    )
    base.update(kw)
    return L.Inputs(**base)


def test_homepods_still_ramp_while_denon_is_a_hard_set():
    """Ein Plan, beide Geräte — HomePods gerampt, Denon hart. Unverändert."""
    plan, _ = L.decide_apply(_vol_inp())
    assert plan.homepods_ramp is True
    assert len(plan.homepods_levels) == 16
    assert plan.denon_set == 0.27


# --------------------------------------------------------------------------- #
# 2. Denon: Sofort-Apply am Debounce vorbei
# --------------------------------------------------------------------------- #
def test_take_immediate_denon_extracts_the_hard_set():
    plan, _ = L.decide_apply(_vol_inp())
    value = L.take_immediate_denon(plan)
    assert value == 0.27
    assert plan.denon_set is None   # nicht doppelt schreiben


def test_take_immediate_denon_leaves_the_homepods_ramp_in_the_plan():
    """Die Rampe bleibt im gepufferten Plan und läuft weiter über das Fenster."""
    plan, _ = L.decide_apply(_vol_inp())
    L.take_immediate_denon(plan)
    assert plan.homepods_ramp is True
    assert len(plan.homepods_levels) == 16
    assert plan.has_work is True


def test_take_immediate_denon_is_noop_without_a_denon_set():
    plan, _ = L.decide_apply(_vol_inp(denon_volume=0.27, denon_target=0.27))
    assert plan.denon_set is None
    assert L.take_immediate_denon(plan) is None


def test_take_immediate_denon_can_be_disabled():
    """Rückfallschalter: `denon_immediate=False` → altes Verhalten."""
    plan, _ = L.decide_apply(_vol_inp())
    assert L.take_immediate_denon(plan, enabled=False) is None
    assert plan.denon_set == 0.27


def test_denon_only_plan_becomes_a_noop_after_extraction():
    """Ist der Denon die einzige Arbeit, bleibt fürs Fenster nichts übrig."""
    plan, _ = L.decide_apply(_vol_inp(homepods_volume=0.25, homepods_target=0.25))
    assert plan.has_work is True
    L.take_immediate_denon(plan)
    assert plan.has_work is False


def test_denon_immediate_default_is_on():
    assert C.DEFAULT_DENON_IMMEDIATE is True
    assert L.RampSettings().denon_immediate is True


def test_tv_transition_denon_target_is_a_single_set():
    """TV-Übergang: 0 % → 27 % ist EIN Wert, keine Stufenfolge."""
    plan, _ = L.decide_apply(_vol_inp(denon_volume=0.0, denon_target=0.27))
    assert plan.denon_set == 0.27
    value = L.take_immediate_denon(plan)
    assert isinstance(value, float)


# --------------------------------------------------------------------------- #
# 3. Debounce: latest-wins ohne Starvation
# --------------------------------------------------------------------------- #
def _work_plan():
    p = L.ApplyPlan()
    p.execute = True
    p.homepods_action = C.ACTION_RESUME
    assert p.has_work
    return p


def test_debounce_window_restarts_while_young():
    """Normalfall unverändert: früher Burst konsolidiert weiter zu EINER Aktion."""
    assert L.debounce_decision(_work_plan(), True, window_age_s=1.0, max_wait_s=8.0) == (
        True,
        True,
    )


def test_debounce_window_is_not_extended_past_max_wait():
    """Ein langer Übergangs-Burst darf die Ausführung nicht ewig schieben."""
    update_pending, restart = L.debounce_decision(
        _work_plan(), True, window_age_s=8.0, max_wait_s=8.0
    )
    assert update_pending is True   # latest-wins bleibt
    assert restart is False         # Fenster wird NICHT verlängert


def test_debounce_starvation_cap_needs_a_running_window():
    assert L.debounce_decision(_work_plan(), False, window_age_s=99.0, max_wait_s=8.0) == (
        True,
        True,
    )


def test_debounce_decision_backwards_compatible_without_age():
    """Alt-Aufrufer ohne Alter/Deckel verhalten sich exakt wie vorher."""
    assert L.debounce_decision(_work_plan(), True) == (True, True)
    assert L.debounce_decision(L.ApplyPlan(), True) == (True, False)
    assert L.debounce_decision(L.ApplyPlan(), False) == (False, False)


def test_debounce_noop_still_never_restarts_the_window():
    """FLEET-245 (Grind-Race) bleibt gültig: No-Op puffert, verlängert aber nie."""
    assert L.debounce_decision(L.ApplyPlan(), True, window_age_s=0.1, max_wait_s=8.0) == (
        True,
        False,
    )


def test_burst_is_still_coalesced_into_one_action():
    """Mehrere Pläne im jungen Fenster ⇒ genau EIN gepufferter (letzter) Plan."""
    pending = None
    for _ in range(5):
        plan = _work_plan()
        update_pending, restart = L.debounce_decision(
            plan, True, window_age_s=0.5, max_wait_s=8.0
        )
        assert restart is True
        if update_pending:
            pending = plan
    assert pending is not None


# --------------------------------------------------------------------------- #
# 4. Kein `unknown`-Rückfall bei idempotentem No-Op
# --------------------------------------------------------------------------- #
def _effective(plan, inp):
    """Spiegelt die Sensor-Auflösung des Coordinators."""
    hp = plan.homepods_levels[-1] if plan.homepods_levels else inp.homepods_target
    dn = plan.denon_set if plan.denon_set is not None else inp.denon_target
    return hp, dn


def test_idempotent_plan_keeps_a_known_effective_target():
    inp = _vol_inp(homepods_volume=0.25, homepods_target=0.25,
                   denon_volume=0.27, denon_target=0.27)
    plan, _ = L.decide_apply(inp)
    assert plan.homepods_levels == [] and plan.denon_set is None
    assert _effective(plan, inp) == (0.25, 0.27)


def test_denon_at_zero_does_not_read_as_unknown():
    """22:23:34-Evidenz: Denon steht auf 0 %, Plan ist No-Op → Ziel bleibt 0.0."""
    inp = _vol_inp(denon_volume=0.0, denon_target=0.0)
    plan, _ = L.decide_apply(inp)
    _, dn = _effective(plan, inp)
    assert dn == 0.0 and dn is not None


def test_missing_policy_target_still_reads_as_unknown():
    """Gegenprobe: gibt es WIRKLICH kein Ziel, bleibt `unknown` korrekt."""
    inp = _vol_inp(homepods_target=None, denon_target=None)
    plan, _ = L.decide_apply(inp)
    assert _effective(plan, inp) == (None, None)


def test_effective_denon_target_survives_the_immediate_extraction():
    """Der Sofort-Set darf den Sensor nicht auf `unknown` fallen lassen."""
    inp = _vol_inp()
    plan, _ = L.decide_apply(inp)
    _, dn_before = _effective(plan, inp)
    L.take_immediate_denon(plan)
    _, dn_after = _effective(plan, inp)
    assert dn_before == 0.27
    assert dn_after == 0.27   # Fallback auf das Policy-Ziel greift


# --------------------------------------------------------------------------- #
# 5. Bestehende Gates bleiben wirksam
# --------------------------------------------------------------------------- #
def test_quiet_mode_still_bypasses_the_debounce():
    p = L.ApplyPlan()
    p.execute = True
    p.quiet_override = True
    assert L.execution_mode(p) == C.EXEC_IMMEDIATE


def test_quiet_volume_stays_direct_not_ramped():
    """Quiet duckt weiterhin hart (kein Ramp) — Volume-Limits bleiben wirksam."""
    inp = _vol_inp(quiet_mode=True, homepods_volume=0.4, homepods_target=0.10)
    plan, _ = L.decide_apply(inp)
    assert plan.homepods_ramp is False
    assert plan.homepods_levels == [0.10]


def test_shadow_mode_executes_nothing():
    plan, _ = L.decide_apply(_vol_inp(apply_enabled=False))
    assert L.execution_mode(plan) == C.EXEC_SHADOW
