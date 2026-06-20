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


def _plan(inp, state=None, settings=None):
    """Nur den Plan (decide_apply gibt seit Phase 2 (plan, state) zurück)."""
    return L.decide_apply(inp, state, settings)[0]


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
    p = _plan(_inp(action=C.ACTION_PAUSE, homepods_should_pause=True,
                            homepods_state="playing"))
    assert p.homepods_action == C.ACTION_PAUSE


def test_action_pause_idempotent_when_not_playing():
    p = _plan(_inp(action=C.ACTION_PAUSE, homepods_should_pause=True,
                            homepods_state="paused"))
    assert p.homepods_action == C.ACTION_NONE


def test_action_resume_when_paused():
    p = _plan(_inp(action=C.ACTION_RESUME, homepods_resume_allowed=True,
                            homepods_state="paused"))
    assert p.homepods_action == C.ACTION_RESUME


def test_action_resume_blocked_by_stop_latch():
    p = _plan(_inp(action=C.ACTION_RESUME, homepods_resume_allowed=True,
                            homepods_state="paused", stop_latch=True))
    assert p.homepods_action == C.ACTION_NONE


def test_action_start_radio_when_idle():
    p = _plan(_inp(action=C.ACTION_START_RADIO, homepods_state="idle"))
    assert p.homepods_action == C.ACTION_START_RADIO


# -------------------------------------------------------------------- Volume
def test_volume_ramp_up():
    p = _plan(_inp(homepods_state="playing", homepods_volume=0.2,
                            homepods_target=0.5))
    assert p.homepods_ramp is True
    assert p.homepods_levels[-1] == 0.5


def test_volume_direct_on_tiny_delta():
    p = _plan(_inp(homepods_volume=0.49, homepods_target=0.5))
    assert p.homepods_ramp is False
    assert p.homepods_levels == [0.5]


def test_volume_idempotent_when_at_target():
    p = _plan(_inp(homepods_volume=0.5, homepods_target=0.5))
    assert p.homepods_levels == []


def test_volume_quiet_is_direct_no_ramp():
    p = _plan(_inp(quiet_mode=True, homepods_volume=0.5, homepods_target=0.10))
    assert p.quiet_override is True
    assert p.homepods_ramp is False
    assert p.homepods_levels == [0.1]


def test_volume_not_allowed_skips_volume():
    p = _plan(_inp(volume_apply_allowed=False, homepods_volume=0.2,
                            homepods_target=0.5))
    assert p.homepods_levels == []
    assert "volume:not_allowed" in p.reasons


def test_volume_skipped_when_player_unavailable():
    p = _plan(_inp(homepods_state="unavailable", homepods_target=0.5))
    assert p.homepods_levels == []


# --------------------------------------------------------------------- Denon
def test_denon_hard_set():
    p = _plan(_inp(denon_state="on", denon_volume=0.3, denon_target=0.4))
    assert p.denon_set == 0.4


def test_denon_idempotent():
    p = _plan(_inp(denon_state="on", denon_volume=0.4, denon_target=0.4))
    assert p.denon_set is None


# ----------------------------------------------------------------- Subwoofer
def test_subwoofer_turn_on():
    p = _plan(_inp(subwoofer_state="off", subwoofer_allowed=True))
    assert p.subwoofer_set is True


def test_subwoofer_turn_off():
    p = _plan(_inp(subwoofer_state="on", subwoofer_allowed=False))
    assert p.subwoofer_set is False


def test_subwoofer_idempotent():
    p = _plan(_inp(subwoofer_state="on", subwoofer_allowed=True))
    assert p.subwoofer_set is None


# ------------------------------------------------------------- Shadow-Gate
def test_shadow_computes_plan_but_does_not_execute():
    p = _plan(_inp(apply_enabled=False, homepods_volume=0.2,
                            homepods_target=0.5))
    assert p.execute is False
    assert p.homepods_levels[-1] == 0.5   # Plan berechnet (Debug)
    assert "shadow:apply_disabled" in p.reasons


def test_execute_true_when_apply_enabled():
    p = _plan(_inp(apply_enabled=True))
    assert p.execute is True


# ------------------------------------------------- R20 Quiet-Snapshot / Restore
def test_r20_quiet_entry_snapshots_pre_quiet_target():
    # tick1: normal → last_homepods_target = 0.45 gemerkt.
    _, s1 = L.decide_apply(_inp(quiet_mode=False, homepods_target=0.45))
    assert s1.last_homepods_target == 0.45
    # tick2: Quiet-Eintritt (Policy duckt auf 0.10) → Snapshot = Pre-Quiet 0.45.
    _, s2 = L.decide_apply(_inp(quiet_mode=True, homepods_target=0.10), s1)
    assert s2.pre_quiet_homepods == 0.45
    assert s2.was_quiet is True


def test_r20_quiet_exit_restores_with_ramp_and_clears_snapshot():
    state = L.ApplyState(was_quiet=True, pre_quiet_homepods=0.45)
    p, s = L.decide_apply(
        _inp(quiet_mode=False, homepods_volume=0.10, homepods_target=0.45), state
    )
    assert p.is_restore is True
    assert p.homepods_ramp is True
    assert p.homepods_levels[-1] == 0.45        # Ramp-Up auf Pre-Quiet
    assert "restore:r20_quiet_end" in p.reasons
    assert s.pre_quiet_homepods is None          # Snapshot freigegeben


def test_r20_denon_restored_hard():
    state = L.ApplyState(was_quiet=True, pre_quiet_homepods=0.45, pre_quiet_denon=0.30)
    p, _ = L.decide_apply(
        _inp(quiet_mode=False, homepods_volume=0.10, homepods_target=0.45,
             denon_volume=0.10, denon_target=0.30), state
    )
    assert p.denon_set == 0.30                   # Denon hart, kein Ramp


def test_r20_no_snapshot_falls_back_to_phase1():
    # Quiet-Exit ohne Snapshot → normaler Phase-1-Ramp auf Policy-Target, kein Restore.
    state = L.ApplyState(was_quiet=True, pre_quiet_homepods=None)
    p, _ = L.decide_apply(
        _inp(quiet_mode=False, homepods_volume=0.10, homepods_target=0.45), state
    )
    assert p.is_restore is False
    assert p.homepods_levels[-1] == 0.45         # Phase 1 rampt trotzdem hoch


# ================================================================= #
# Phase 3 — Denon-Nachlauf (R13/R14)
# ================================================================= #
def _ninp(**kw):
    """Inputs nur mit den Nachlauf-relevanten Feldern (Rest neutral).

    `denon_consumer_active=False` ist der neutrale Default (kein anderer Denon-
    Konsument) — FLEET-80-Gate-Tests setzen ihn explizit auf True."""
    base = dict(
        pc_power_on=None, tv_power_on=None, denon_power_on=None, bio_sleep=None,
        denon_consumer_active=False,
    )
    base.update(kw)
    return L.Inputs(**base)


# ---------------------------------------------------------------- R13 (PC)
def test_r13_arm_when_pc_off_and_denon_on():
    # Fallflanke PC an→aus (FLEET-80: nur die Flanke armt, nicht Steady-State).
    st = L.NachlaufState(last_pc_on=True)
    p, s = L.decide_denon_nachlauf(_ninp(pc_power_on=False, denon_power_on=True), st)
    assert p.pc == L.TIMER_ARM
    assert s.pc_armed is True
    assert "r13:arm_pc" in p.reasons


def test_r13_no_arm_steady_state_pc_off_fleet80():
    # FLEET-80: PC dauerhaft aus (kein on→off-Edge, z.B. beim TV-Schauen) → KEIN
    # Arm/Re-Arm → kein 90s-Denon-Off-Loop.
    st = L.NachlaufState(last_pc_on=False)
    p, s = L.decide_denon_nachlauf(_ninp(pc_power_on=False, denon_power_on=True), st)
    assert p.pc == L.TIMER_NONE
    assert s.pc_armed is False


def test_r13_no_arm_when_pc_on():
    p, s = L.decide_denon_nachlauf(_ninp(pc_power_on=True, denon_power_on=True))
    assert p.pc == L.TIMER_NONE
    assert s.pc_armed is False


def test_r13_no_arm_when_denon_off():
    p, s = L.decide_denon_nachlauf(_ninp(pc_power_on=False, denon_power_on=False))
    assert p.pc == L.TIMER_NONE
    assert s.pc_armed is False


def test_r13_no_arm_on_unknown_inputs():
    # PC/Denon ungebunden (None) → niemals armen.
    p, s = L.decide_denon_nachlauf(_ninp(pc_power_on=None, denon_power_on=None))
    assert p.pc == L.TIMER_NONE
    assert s.pc_armed is False


def test_r13_idempotent_while_armed():
    st = L.NachlaufState(pc_armed=True)
    p, s = L.decide_denon_nachlauf(_ninp(pc_power_on=False, denon_power_on=True), st)
    assert p.pc == L.TIMER_NONE          # kein Re-Arm
    assert s.pc_armed is True


def test_r13_cancel_when_pc_returns():
    st = L.NachlaufState(pc_armed=True)
    p, s = L.decide_denon_nachlauf(_ninp(pc_power_on=True, denon_power_on=True), st)
    assert p.pc == L.TIMER_CANCEL
    assert s.pc_armed is False


def test_r13_cancel_when_denon_goes_off():
    st = L.NachlaufState(pc_armed=True)
    p, s = L.decide_denon_nachlauf(_ninp(pc_power_on=False, denon_power_on=False), st)
    assert p.pc == L.TIMER_CANCEL
    assert s.pc_armed is False


def test_r13_cancel_when_inputs_go_unknown():
    st = L.NachlaufState(pc_armed=True)
    p, s = L.decide_denon_nachlauf(_ninp(pc_power_on=None, denon_power_on=None), st)
    assert p.pc == L.TIMER_CANCEL        # kein Off auf Basis fehlender Daten
    assert s.pc_armed is False


# ---------------------------------------------------------------- R14 (TV)
def test_r14_arm_when_tv_off_and_denon_on():
    st = L.NachlaufState(last_tv_on=True)   # Fallflanke TV an→aus (FLEET-80)
    p, s = L.decide_denon_nachlauf(_ninp(tv_power_on=False, denon_power_on=True), st)
    assert p.tv == L.TIMER_ARM
    assert s.tv_armed is True


def test_r14_cancel_when_tv_returns():
    st = L.NachlaufState(tv_armed=True)
    p, s = L.decide_denon_nachlauf(_ninp(tv_power_on=True, denon_power_on=True), st)
    assert p.tv == L.TIMER_CANCEL
    assert s.tv_armed is False


def test_r14_sleep_pauses_armed_timer():
    st = L.NachlaufState(tv_armed=True)
    p, s = L.decide_denon_nachlauf(
        _ninp(tv_power_on=False, denon_power_on=True, bio_sleep=True), st
    )
    assert p.tv == L.TIMER_PAUSE
    assert s.tv_armed is True             # bleibt armed (nur ausgesetzt)
    assert s.tv_paused is True


def test_r14_no_arm_during_sleep():
    p, s = L.decide_denon_nachlauf(
        _ninp(tv_power_on=False, denon_power_on=True, bio_sleep=True)
    )
    assert p.tv == L.TIMER_NONE
    assert s.tv_armed is False


def test_r14_resume_restart_after_sleep_ends():
    st = L.NachlaufState(tv_armed=True, tv_paused=True)
    p, s = L.decide_denon_nachlauf(
        _ninp(tv_power_on=False, denon_power_on=True, bio_sleep=False), st
    )
    assert p.tv == L.TIMER_ARM            # Neustart
    assert s.tv_paused is False
    assert "r14:resume_tv" in p.reasons


def test_r14_paused_then_tv_returns_cancels_after_sleep():
    st = L.NachlaufState(tv_armed=True, tv_paused=True)
    p, s = L.decide_denon_nachlauf(
        _ninp(tv_power_on=True, denon_power_on=True, bio_sleep=False), st
    )
    assert p.tv == L.TIMER_CANCEL
    assert s.tv_armed is False
    assert s.tv_paused is False


# -------------------------------------------- FLEET-80 Cross-Source-Gate
def test_r13_no_arm_when_other_consumer_active():
    # Wurzel-Szenario letzte Nacht: PC-Aus-Flanke, aber der TV nutzt den Denon
    # (media_device=tv → denon_consumer_active). R13 darf NICHT armen.
    st = L.NachlaufState(last_pc_on=True)
    p, s = L.decide_denon_nachlauf(
        _ninp(pc_power_on=False, denon_power_on=True, denon_consumer_active=True), st
    )
    assert p.pc == L.TIMER_NONE
    assert s.pc_armed is False


def test_r13_cancel_when_consumer_becomes_active():
    # R13 läuft, dann wird der TV aktiviert (braucht den Denon) → Timer cancel.
    st = L.NachlaufState(pc_armed=True)
    p, s = L.decide_denon_nachlauf(
        _ninp(pc_power_on=False, denon_power_on=True, denon_consumer_active=True), st
    )
    assert p.pc == L.TIMER_CANCEL
    assert s.pc_armed is False


def test_r13_no_arm_when_consumer_unknown_conservative():
    # media_device ungebunden/unbekannt (None) → konservativ wie „Konsument aktiv":
    # kein Denon-Off auf Basis fehlender Daten.
    st = L.NachlaufState(last_pc_on=True)
    p, s = L.decide_denon_nachlauf(
        _ninp(pc_power_on=False, denon_power_on=True, denon_consumer_active=None), st
    )
    assert p.pc == L.TIMER_NONE
    assert s.pc_armed is False


def test_r14_no_arm_when_other_consumer_active():
    # TV-Aus-Flanke, aber der PC nutzt den Denon weiter (media_device=pc).
    st = L.NachlaufState(last_tv_on=True)
    p, s = L.decide_denon_nachlauf(
        _ninp(tv_power_on=False, denon_power_on=True, denon_consumer_active=True), st
    )
    assert p.tv == L.TIMER_NONE
    assert s.tv_armed is False


def test_r14_cancel_when_consumer_becomes_active():
    st = L.NachlaufState(tv_armed=True)
    p, s = L.decide_denon_nachlauf(
        _ninp(tv_power_on=False, denon_power_on=True, denon_consumer_active=True), st
    )
    assert p.tv == L.TIMER_CANCEL
    assert s.tv_armed is False


# ---------------------------------------------------------------- shared
def test_pc_and_tv_independent():
    st = L.NachlaufState(last_pc_on=True, last_tv_on=True)   # beide Fallflanken
    p, s = L.decide_denon_nachlauf(
        _ninp(pc_power_on=False, tv_power_on=False, denon_power_on=True), st
    )
    assert p.pc == L.TIMER_ARM
    assert p.tv == L.TIMER_ARM
    assert p.active is True


def test_plan_inactive_when_no_change():
    p, _ = L.decide_denon_nachlauf(_ninp(pc_power_on=True, denon_power_on=True))
    assert p.active is False
    assert p.as_dict() == {"pc": L.TIMER_NONE, "tv": L.TIMER_NONE, "reasons": []}


# ----------------------------------------------- R2/R3 execution_mode + has_work
def test_exec_mode_shadow_when_apply_disabled():
    p = L.ApplyPlan(execute=False, homepods_levels=[0.4])
    assert L.execution_mode(p) == C.EXEC_SHADOW


def test_exec_mode_debounce_normal_case():
    p = L.ApplyPlan(execute=True, homepods_levels=[0.4])
    assert L.execution_mode(p) == C.EXEC_DEBOUNCE


def test_exec_mode_immediate_when_quiet_breaks_through():
    # R2/R3-Ausnahme: Quiet bricht sofort durch, kein Debounce.
    p = L.ApplyPlan(execute=True, quiet_override=True, homepods_levels=[0.1])
    assert L.execution_mode(p) == C.EXEC_IMMEDIATE


def test_exec_mode_quiet_immediate_even_without_levels():
    # Quiet ohne Volume-Änderung muss trotzdem sofort laufen (Ramp-Abbruch).
    p = L.ApplyPlan(execute=True, quiet_override=True)
    assert L.execution_mode(p) == C.EXEC_IMMEDIATE


def test_exec_mode_immediate_on_restore_quiet_end():
    # FLEET-81: Quiet-Ende-Restore (is_restore, quiet_override schon False) muss
    # SOFORT laufen — sonst hängt der Pegel nach Tür-zu das volle Debounce-Fenster
    # auf ducked_target, bevor der Un-Duck landet.
    p = L.ApplyPlan(execute=True, quiet_override=False, is_restore=True,
                    denon_set=0.25, homepods_levels=[0.45])
    assert L.execution_mode(p) == C.EXEC_IMMEDIATE


def test_exec_mode_restore_immediate_shadow_still_wins():
    # is_restore hebelt das Shadow-Gate NICHT aus (apply_enabled bleibt König).
    p = L.ApplyPlan(execute=False, is_restore=True, denon_set=0.25)
    assert L.execution_mode(p) == C.EXEC_SHADOW


def test_has_work_true_for_each_actionable_field():
    assert L.ApplyPlan(homepods_action=C.ACTION_PAUSE).has_work is True
    assert L.ApplyPlan(homepods_levels=[0.3]).has_work is True
    assert L.ApplyPlan(denon_set=0.2).has_work is True
    assert L.ApplyPlan(subwoofer_set=False).has_work is True   # False ≠ None → Arbeit


def test_has_work_false_for_trivial_plan():
    # Reines Re-Eval ohne Soll≠Ist (auch quiet_override allein) ist keine Arbeit
    # → darf ein laufendes Debounce-Fenster nicht neu anstoßen.
    assert L.ApplyPlan().has_work is False
    assert L.ApplyPlan(quiet_override=True).has_work is False


# --------------------------------------------- Phase 4b: Radio-Katalog-Port
def test_resolve_radio_uri_known_station():
    assert L.resolve_radio_uri("1live") == C.RADIO_CATALOG["1live"]
    assert L.resolve_radio_uri("jack_fm_berlin").startswith("radiobrowser://radio/")


def test_resolve_radio_uri_unknown_or_none():
    assert L.resolve_radio_uri("does_not_exist") is None
    assert L.resolve_radio_uri(None) is None
    assert L.resolve_radio_uri("") is None


def test_start_radio_resolves_uri_from_station():
    p = _plan(_inp(action=C.ACTION_START_RADIO, homepods_state="idle", radio_station="wdr4"))
    assert p.homepods_action == C.ACTION_START_RADIO
    assert p.radio_uri == C.RADIO_CATALOG["wdr4"]


def test_start_radio_unbound_gates_still_allowed():
    # radio_ready/manual_playback ungebunden (None) → non-regressiv erlauben.
    p = _plan(_inp(action=C.ACTION_START_RADIO, homepods_state="idle",
                   radio_station="gayfm", radio_ready=None, manual_playback=None))
    assert p.homepods_action == C.ACTION_START_RADIO


def test_start_radio_blocked_when_not_ready():
    p = _plan(_inp(action=C.ACTION_START_RADIO, homepods_state="idle",
                   radio_station="gayfm", radio_ready=False))
    assert p.homepods_action == C.ACTION_NONE


def test_start_radio_blocked_when_manual_playback():
    p = _plan(_inp(action=C.ACTION_START_RADIO, homepods_state="idle",
                   radio_station="gayfm", manual_playback=True))
    assert p.homepods_action == C.ACTION_NONE


def test_start_radio_unknown_station_no_uri_falls_back():
    # Sender unbekannt → action bleibt start_radio, aber radio_uri None
    # (Coordinator delegiert dann ans YAML-Script).
    p = _plan(_inp(action=C.ACTION_START_RADIO, homepods_state="idle", radio_station="xyz"))
    assert p.homepods_action == C.ACTION_START_RADIO
    assert p.radio_uri is None


# ------------------------------------------------- Phase 4c: TV-WoL (R12)
def _twol(**kw):
    base = dict(media_device=None, tv_player_state=None, tv_power_on=None)
    base.update(kw)
    return L.Inputs(**base)


def test_tv_wol_fires_on_screen_when_tv_off():
    p, s = L.decide_tv_wol(_twol(media_device="tv", tv_player_state="off"))
    assert p.fire is True
    assert s.fired is True
    assert "r12:tv_on" in p.reasons


def test_tv_wol_fires_for_appletv_too():
    p, _ = L.decide_tv_wol(_twol(media_device="appletv", tv_player_state="standby"))
    assert p.fire is True


def test_tv_wol_no_refire_while_armed():
    s = L.TvWolState(fired=True)
    p, ns = L.decide_tv_wol(_twol(media_device="tv", tv_player_state="off"), s)
    assert p.fire is False
    assert ns.fired is True


def test_tv_wol_resets_when_tv_turns_on():
    s = L.TvWolState(fired=True)
    p, ns = L.decide_tv_wol(_twol(media_device="tv", tv_player_state="playing"), s)
    assert p.fire is False
    assert ns.fired is False


def test_tv_wol_resets_when_leaving_screen():
    s = L.TvWolState(fired=True)
    p, ns = L.decide_tv_wol(_twol(media_device="pc", tv_player_state="off"), s)
    assert p.fire is False
    assert ns.fired is False


def test_tv_wol_no_fire_for_pc():
    p, _ = L.decide_tv_wol(_twol(media_device="pc", tv_player_state="off"))
    assert p.fire is False


def test_tv_wol_no_fire_on_unknown_tv_state():
    # WebOS ungebunden + keine Wattage → fail-safe, nicht feuern.
    p, _ = L.decide_tv_wol(_twol(media_device="tv", tv_player_state=None, tv_power_on=None))
    assert p.fire is False


def test_tv_wol_wattage_fallback_fires():
    # WebOS unavailable → Wattage-Fallback (tv_power_on False = aus) → feuern.
    p, _ = L.decide_tv_wol(_twol(media_device="tv", tv_player_state="unavailable", tv_power_on=False))
    assert p.fire is True


def test_tv_wol_webos_priority_over_wattage():
    # WebOS sagt an (playing) → kein Feuern, auch wenn Wattage "aus" meldet.
    p, _ = L.decide_tv_wol(_twol(media_device="tv", tv_player_state="playing", tv_power_on=False))
    assert p.fire is False


# ------------------------------------------------- Phase 3b: Sleep-TV-Off (R24)
def _stv(**kw):
    base = dict(bio_sleep=None, tv_player_state=None, tv_power_on=None,
               sleep_tv_extend_pressed=False)
    base.update(kw)
    return L.Inputs(**base)


def test_sleep_tv_arms_when_sleep_and_tv_on():
    p, s = L.decide_sleep_tv(_stv(bio_sleep=True, tv_player_state="playing"))
    assert p.intent == L.TIMER_ARM
    assert s.armed is True


def test_sleep_tv_no_arm_when_tv_off():
    p, s = L.decide_sleep_tv(_stv(bio_sleep=True, tv_player_state="off"))
    assert p.intent == L.TIMER_NONE
    assert s.armed is False


def test_sleep_tv_no_arm_when_not_sleep():
    p, _ = L.decide_sleep_tv(_stv(bio_sleep=False, tv_player_state="playing"))
    assert p.intent == L.TIMER_NONE


def test_sleep_tv_no_arm_on_unknown_tv():
    p, _ = L.decide_sleep_tv(_stv(bio_sleep=True, tv_player_state=None, tv_power_on=None))
    assert p.intent == L.TIMER_NONE


def test_sleep_tv_extend_when_armed_and_pressed():
    s = L.SleepTvState(armed=True)
    p, ns = L.decide_sleep_tv(
        _stv(bio_sleep=True, tv_player_state="playing", sleep_tv_extend_pressed=True), s)
    assert p.intent == L.TIMER_EXTEND
    assert ns.armed is True


def test_sleep_tv_cancel_when_sleep_ends():
    s = L.SleepTvState(armed=True)
    p, ns = L.decide_sleep_tv(_stv(bio_sleep=False, tv_player_state="playing"), s)
    assert p.intent == L.TIMER_CANCEL
    assert ns.armed is False


def test_sleep_tv_cancel_when_tv_off():
    s = L.SleepTvState(armed=True)
    p, ns = L.decide_sleep_tv(_stv(bio_sleep=True, tv_player_state="off"), s)
    assert p.intent == L.TIMER_CANCEL
    assert ns.armed is False


# ----------------------------------------------------- R23: Wake-Sequenz
def test_wake_fires_on_trigger_when_not_sleep():
    p = L.decide_wake(L.Inputs(wake_trigger_fired=True, bio_sleep=False))
    assert p.fire is True
    assert "r23:wake" in p.reasons


def test_wake_fires_when_bio_unknown():
    # bio ungebunden (None) ≠ sleep → erlaubt.
    p = L.decide_wake(L.Inputs(wake_trigger_fired=True, bio_sleep=None))
    assert p.fire is True


def test_wake_suppressed_during_sleep():
    p = L.decide_wake(L.Inputs(wake_trigger_fired=True, bio_sleep=True))
    assert p.fire is False
    assert "r23:suppressed_sleep" in p.reasons


def test_wake_no_fire_without_trigger():
    p = L.decide_wake(L.Inputs(wake_trigger_fired=False, bio_sleep=False))
    assert p.fire is False
    assert p.reasons == []


# --------------------------------------------- FLEET-79: Radio-Autostart-Gate
def test_autostart_radio_ok_when_ready_idle():
    assert L.should_autostart_radio(L.Inputs(
        radio_ready=True, manual_playback=False, planned_station_playing=False)) is True


def test_autostart_radio_blocked_when_not_ready():
    assert L.should_autostart_radio(L.Inputs(
        radio_ready=False, manual_playback=False, planned_station_playing=False)) is False
    # radio_ready ungebunden (None) → ebenfalls blockt
    assert L.should_autostart_radio(L.Inputs(
        radio_ready=None, manual_playback=False, planned_station_playing=False)) is False


def test_autostart_radio_blocked_during_manual():
    assert L.should_autostart_radio(L.Inputs(
        radio_ready=True, manual_playback=True, planned_station_playing=False)) is False


def test_autostart_radio_blocked_when_planned_already_playing():
    assert L.should_autostart_radio(L.Inputs(
        radio_ready=True, manual_playback=False, planned_station_playing=True)) is False


def test_radio_defaults_shape_and_sort():
    d = L.radio_defaults()
    assert len(d) == len(C.RADIO_CATALOG)
    assert all({"key", "name", "uri"} <= set(s) for s in d)
    # nach Anzeigenamen sortiert (1LIVE vor WDR …)
    names = [s["name"].lower() for s in d]
    assert names == sorted(names)
    # jeder Eintrag trägt die korrekte URI aus dem Katalog
    assert all(s["uri"] == C.RADIO_CATALOG[s["key"]] for s in d)
