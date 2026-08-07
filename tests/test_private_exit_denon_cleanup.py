"""benni_media#14 — Denon-Lifecycle beim Übergang private_time → HomePod-Musik.

Belegter Ablauf (Recorder-Evidenz, 2026-07-31):

- ``01:08:18.845`` ``sensor.benni_master_pc`` → ``off`` (``powered=false``, 17 W).
- ``01:08:25.092`` ``media_context``  ``private_time`` → ``idle``.
- ``01:08:25.097`` ``audio_owner``    ``private_stack`` → ``none`` (Exit-Flanke).
- ``01:08:25.105`` ``media_device``   ``pc`` → ``denon``  (**8 ms zu spät**).
- ``sensor.benni_master_denon`` blieb den ganzen Zeitraum ``active``.

Im Exit-Tick las das Konsumenten-Gate also noch ``media_device == "pc"`` und
verwarf die Flanke. Sie kommt nicht wieder → der Denon blieb an. Diese Tests
halten genau diesen Übergang fest — die Übergangs-LATENZ gehört zu #13.
"""
from __future__ import annotations

import bma_const as C
import bma_logic as L


def _inp(**kw):
    """Zustand direkt nach dem Private-Exit: PC aus, Denon an, TV aus."""
    base = dict(
        private_active=False,
        denon_power_on=True,
        pc_power_on=False,
        tv_power_on=False,
        tv_player_state="off",
        media_device="pc",              # stale Label (wechselt 8 ms später)
        denon_consumer_active=True,     # weil media_device noch "pc" sagt
    )
    base.update(kw)
    return L.Inputs(**base)


# --------------------------------------------------------------------------- #
# 1. Das Konsumenten-Gate (Kern des Fixes)
# --------------------------------------------------------------------------- #
def test_powered_off_pc_does_not_hold_the_shared_denon():
    """Kern von #14: ein nachweislich ausgeschalteter PC hält den Denon nicht."""
    assert L.denon_consumer_holds(_inp()) is False


def test_powered_on_pc_still_holds_the_denon():
    """Gegenprobe: läuft der PC wirklich, bleibt der Denon geschützt."""
    assert L.denon_consumer_holds(_inp(pc_power_on=True)) is True


def test_unknown_pc_power_stays_conservative():
    """FLEET-80-Sicherheitslinie: kein Off auf Basis fehlender Daten."""
    assert L.denon_consumer_holds(_inp(pc_power_on=None)) is True


def test_powered_off_tv_does_not_hold_the_denon():
    assert (
        L.denon_consumer_holds(
            _inp(media_device="tv", tv_player_state="off", tv_power_on=False)
        )
        is False
    )


def test_running_tv_still_holds_the_denon():
    assert (
        L.denon_consumer_holds(
            _inp(media_device="tv", tv_player_state="playing", tv_power_on=True)
        )
        is True
    )


def test_consumers_without_own_power_source_stay_conservative():
    """appletv/ps5/switch haben keine unabhängige Power-Wahrheit → unverändert."""
    for device in ("appletv", "ps5", "switch"):
        assert L.denon_consumer_holds(_inp(media_device=device)) is True, device


def test_gate_passes_through_false_and_none_unchanged():
    """Kein Konsument / unbekannt bleiben exakt wie vorher."""
    assert L.denon_consumer_holds(_inp(denon_consumer_active=False)) is False
    assert L.denon_consumer_holds(_inp(denon_consumer_active=None)) is None


# --------------------------------------------------------------------------- #
# 2. Private-Exit: der belegte Übergang
# --------------------------------------------------------------------------- #
def _was_private():
    return L.PrivateExitState(was_private=True, armed=False)


def test_private_exit_arms_denon_off_despite_stale_pc_label():
    """Der belegte 8-ms-Race: die Exit-Flanke darf NICHT verschluckt werden."""
    plan, state = L.decide_private_exit(_inp(), _was_private())
    assert plan.timer == L.TIMER_ARM
    assert state.armed is True
    assert "arm:denon_off_delay" in plan.reasons


def test_private_exit_regression_edge_is_not_silently_dropped():
    """Vor #14 endete derselbe Tick in `no_delay:tv_or_consumer` — nie wieder."""
    plan, _ = L.decide_private_exit(_inp(), _was_private())
    assert "no_delay:tv_or_consumer" not in plan.reasons


def test_private_exit_suppresses_homepods_while_denon_still_on():
    """control#3 bleibt gültig: kein kurzer Parallelbetrieb HomePods + Denon."""
    plan, _ = L.decide_private_exit(_inp(), _was_private())
    assert plan.suppress_homepods is True


def test_private_exit_after_label_settled_on_denon_also_arms():
    """8 ms später steht media_device auf `denon` — Ergebnis muss identisch sein."""
    inp = _inp(media_device="denon", denon_consumer_active=False)
    plan, state = L.decide_private_exit(inp, _was_private())
    assert plan.timer == L.TIMER_ARM
    assert state.armed is True


def test_private_exit_still_defers_to_a_genuinely_running_tv():
    """Shared-Sink-Regel bleibt: läuft der TV wirklich, kein Denon-Off."""
    inp = _inp(media_device="tv", tv_player_state="playing", tv_power_on=True)
    plan, state = L.decide_private_exit(inp, _was_private())
    assert plan.timer != L.TIMER_ARM
    assert state.armed is False
    assert "no_delay:tv_or_consumer" in plan.reasons


def test_private_exit_defers_to_running_ps5():
    """Games-Konsole darf nicht regressieren (AC #14)."""
    inp = _inp(media_device="ps5")
    plan, state = L.decide_private_exit(inp, _was_private())
    assert plan.timer != L.TIMER_ARM
    assert state.armed is False


def test_private_exit_no_delay_when_denon_already_off():
    inp = _inp(denon_power_on=False)
    plan, state = L.decide_private_exit(inp, _was_private())
    assert plan.timer == L.TIMER_NONE
    assert state.armed is False
    assert "no_delay:denon_already_off" in plan.reasons


def test_private_reentry_cancels_a_running_delay():
    """Private beginnt neu → laufender Off-Delay wird abgebrochen."""
    inp = _inp(private_active=True)
    state = L.PrivateExitState(was_private=False, armed=True)
    plan, ns = L.decide_private_exit(inp, state)
    assert plan.timer == L.TIMER_CANCEL
    assert ns.armed is False


def test_running_delay_is_cancelled_when_a_real_consumer_appears():
    """TV übernimmt während des Delays → kein Off (Shared-Sink-Schutz)."""
    inp = _inp(media_device="tv", tv_player_state="playing", tv_power_on=True)
    state = L.PrivateExitState(was_private=False, armed=True)
    plan, ns = L.decide_private_exit(inp, state)
    assert plan.timer == L.TIMER_CANCEL
    assert ns.armed is False


def test_no_start_stop_loop_after_the_delay_was_armed():
    """Folge-Tick ohne Flanke darf nicht erneut armen (kein Arm/Cancel-Loop)."""
    first, state = L.decide_private_exit(_inp(), _was_private())
    assert first.timer == L.TIMER_ARM
    second, state2 = L.decide_private_exit(_inp(), state)
    assert second.timer == L.TIMER_NONE
    assert state2.armed is True


# --------------------------------------------------------------------------- #
# 3. R13-Nachlauf teilt dasselbe Gate — Regressionsschutz
# --------------------------------------------------------------------------- #
def test_r13_pc_nachlauf_arms_despite_stale_pc_label():
    """Dasselbe stale Label blockierte auch das zweite Sicherheitsnetz."""
    inp = _inp()
    state = L.NachlaufState(last_pc_on=True, last_tv_on=False)
    plan, ns = L.decide_denon_nachlauf(inp, state)
    assert plan.pc == L.TIMER_ARM
    assert ns.pc_armed is True


def test_r13_pc_nachlauf_does_not_arm_while_pc_runs():
    inp = _inp(pc_power_on=True)
    state = L.NachlaufState(last_pc_on=True, last_tv_on=False)
    plan, ns = L.decide_denon_nachlauf(inp, state)
    assert plan.pc != L.TIMER_ARM
    assert ns.pc_armed is False


def test_r14_tv_nachlauf_still_defers_to_a_running_consumer():
    """TV-Nachlauf darf einen echten PS5-Konsumenten weiterhin nicht überfahren."""
    inp = _inp(media_device="ps5", tv_power_on=False, tv_player_state="off")
    state = L.NachlaufState(last_tv_on=True, last_pc_on=False)
    plan, ns = L.decide_denon_nachlauf(inp, state)
    assert plan.tv != L.TIMER_ARM
    assert ns.tv_armed is False


# --------------------------------------------------------------------------- #
# 4. Subwoofer-Cleanup (AC #14)
# --------------------------------------------------------------------------- #
def _apply_inp(**kw):
    base = dict(
        apply_enabled=True,
        volume_apply_allowed=True,
        action=C.ACTION_NONE,
        homepods_configured=True,
        homepods_state="playing",
        homepods_volume=0.25,
        homepods_target=0.25,
        denon_configured=True,
        denon_state="on",
        denon_volume=0.2,
        denon_target=0.2,
        subwoofer_configured=True,
        subwoofer_state="on",
        subwoofer_allowed=False,
    )
    base.update(kw)
    return L.Inputs(**base)


def test_subwoofer_is_switched_off_when_policy_drops_the_denon_path():
    """Fällt `subwoofer_allowed`, schaltet Apply den Sub aus (Denon-Lifecycle)."""
    plan, _ = L.decide_apply(_apply_inp())
    assert plan.subwoofer_set is False
    assert "subwoofer:off" in plan.reasons


def test_subwoofer_off_is_idempotent():
    """Bereits aus → kein erneuter Schaltbefehl (kein Start/Stop-Loop)."""
    plan, _ = L.decide_apply(_apply_inp(subwoofer_state="off"))
    assert plan.subwoofer_set is None


def test_subwoofer_stays_on_while_a_real_denon_context_needs_it():
    plan, _ = L.decide_apply(_apply_inp(subwoofer_allowed=True))
    assert plan.subwoofer_set is None   # schon an, nichts zu tun


def test_music_keeps_playing_through_homepods_during_the_cleanup():
    """AC: Musik läuft weiter — der Denon-Off pausiert die HomePods nicht."""
    plan, _ = L.decide_apply(_apply_inp())
    assert plan.homepods_action == C.ACTION_NONE
    assert plan.away_block is False
