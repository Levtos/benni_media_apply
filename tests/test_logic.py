"""Pure-logic-Tests für den Apply-Kern (Phase 1, FLEET-40).

Deckt ab: Ramp-Sequenz (tiny/big/idempotent/no-current), Apply-Plan-Gating,
HomePods-Action-Idempotenz (pause/resume/radio + stop_latch), Volume
(Ramp/direct/quiet/idempotent), Denon hart, Subwoofer on/off, Shadow-Gate.
"""
from __future__ import annotations

import bma_const as C
import bma_logic as L


def _inp(**kw):
    base = dict(
        apply_enabled=True,
        volume_apply_allowed=True,
        action=C.ACTION_NONE,
        homepods_configured=True,
        homepods_state="playing",
        homepods_volume=0.4,
        homepods_target=None,
        denon_configured=True,
        denon_state="on",
        denon_volume=0.3,
        denon_target=None,
        subwoofer_configured=True,
        subwoofer_state="off",
        subwoofer_allowed=False,
    )
    base.update(kw)
    return L.Inputs(**base)


# --------------------------------------------------------------- ramp_levels
def test_ramp_big_delta_steps_to_target():
    lv = L.ramp_levels(0.2, 0.5, 16, 0.02)
    assert len(lv) == 16
    assert lv[-1] == 0.5
    assert lv == sorted(lv)              # monoton steigend
    assert all(0.2 < x <= 0.5 for x in lv)


def test_ramp_down_steps_to_target():
    lv = L.ramp_levels(0.6, 0.2, 16, 0.02)
    assert len(lv) == 16
    assert lv[-1] == 0.2
    assert lv == sorted(lv, reverse=True)


def test_ramp_tiny_delta_is_direct():
    assert L.ramp_levels(0.40, 0.41, 16, 0.02) == [0.41]


def test_ramp_equal_is_noop():
    assert L.ramp_levels(0.5, 0.5, 16, 0.02) == []


def test_ramp_no_current_sets_directly():
    assert L.ramp_levels(None, 0.5, 16, 0.02) == [0.5]


def test_ramp_none_target_noop():
    assert L.ramp_levels(0.5, None, 16, 0.02) == []


def test_ramp_clamps_target():
    assert L.ramp_levels(None, 1.4, 16, 0.02) == [1.0]


# ------------------------------------------------------------- HomePods-Action
def test_action_pause_when_playing():
    p = L.decide_apply(_inp(action=C.ACTION_PAUSE, homepods_should_pause=True,
                            homepods_state="playing"))
    assert p.homepods_action == C.ACTION_PAUSE


def test_action_pause_idempotent_when_not_playing():
    p = L.decide_apply(_inp(action=C.ACTION_PAUSE, homepods_should_pause=True,
                            homepods_state="paused"))
    assert p.homepods_action == C.ACTION_NONE


def test_action_resume_when_paused():
    p = L.decide_apply(_inp(action=C.ACTION_RESUME, homepods_resume_allowed=True,
                            homepods_state="paused"))
    assert p.homepods_action == C.ACTION_RESUME


def test_action_resume_blocked_by_stop_latch():
    p = L.decide_apply(_inp(action=C.ACTION_RESUME, homepods_resume_allowed=True,
                            homepods_state="paused", stop_latch=True))
    assert p.homepods_action == C.ACTION_NONE


def test_action_start_radio_when_idle():
    p = L.decide_apply(_inp(action=C.ACTION_START_RADIO, homepods_state="idle"))
    assert p.homepods_action == C.ACTION_START_RADIO


# -------------------------------------------------------------------- Volume
def test_volume_ramp_up():
    p = L.decide_apply(_inp(homepods_state="playing", homepods_volume=0.2,
                            homepods_target=0.5))
    assert p.homepods_ramp is True
    assert p.homepods_levels[-1] == 0.5


def test_volume_direct_on_tiny_delta():
    p = L.decide_apply(_inp(homepods_volume=0.49, homepods_target=0.5))
    assert p.homepods_ramp is False
    assert p.homepods_levels == [0.5]


def test_volume_idempotent_when_at_target():
    p = L.decide_apply(_inp(homepods_volume=0.5, homepods_target=0.5))
    assert p.homepods_levels == []


def test_volume_quiet_is_direct_no_ramp():
    p = L.decide_apply(_inp(quiet_mode=True, homepods_volume=0.5, homepods_target=0.10))
    assert p.quiet_override is True
    assert p.homepods_ramp is False
    assert p.homepods_levels == [0.1]


def test_volume_not_allowed_skips_volume():
    p = L.decide_apply(_inp(volume_apply_allowed=False, homepods_volume=0.2,
                            homepods_target=0.5))
    assert p.homepods_levels == []
    assert "volume:not_allowed" in p.reasons


def test_volume_skipped_when_player_unavailable():
    p = L.decide_apply(_inp(homepods_state="unavailable", homepods_target=0.5))
    assert p.homepods_levels == []


# --------------------------------------------------------------------- Denon
def test_denon_hard_set():
    p = L.decide_apply(_inp(denon_state="on", denon_volume=0.3, denon_target=0.4))
    assert p.denon_set == 0.4


def test_denon_idempotent():
    p = L.decide_apply(_inp(denon_state="on", denon_volume=0.4, denon_target=0.4))
    assert p.denon_set is None


# ----------------------------------------------------------------- Subwoofer
def test_subwoofer_turn_on():
    p = L.decide_apply(_inp(subwoofer_state="off", subwoofer_allowed=True))
    assert p.subwoofer_set is True


def test_subwoofer_turn_off():
    p = L.decide_apply(_inp(subwoofer_state="on", subwoofer_allowed=False))
    assert p.subwoofer_set is False


def test_subwoofer_idempotent():
    p = L.decide_apply(_inp(subwoofer_state="on", subwoofer_allowed=True))
    assert p.subwoofer_set is None


# ------------------------------------------------------------- Shadow-Gate
def test_shadow_computes_plan_but_does_not_execute():
    p = L.decide_apply(_inp(apply_enabled=False, homepods_volume=0.2,
                            homepods_target=0.5))
    assert p.execute is False
    assert p.homepods_levels[-1] == 0.5   # Plan berechnet (Debug)
    assert "shadow:apply_disabled" in p.reasons


def test_execute_true_when_apply_enabled():
    p = L.decide_apply(_inp(apply_enabled=True))
    assert p.execute is True
