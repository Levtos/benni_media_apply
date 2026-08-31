"""Regression contract for #41's managed HomePod unmute hotfix."""
from __future__ import annotations

import asyncio
import ast
from dataclasses import replace
from pathlib import Path

import pytest

import bma_const as C
import bma_logic as L


PODS = ("media_player.pod_1", "media_player.pod_2")


def _managed_inputs(**changes) -> L.Inputs:
    inp = L.Inputs(
        apply_enabled=True,
        volume_apply_allowed=True,
        action=C.ACTION_NONE,
        homepods_should_pause=False,
        homepods_resume_allowed=False,
        homepods_target=0.30,
        quiet_mode=False,
        presence_state="anwesend",
        away_gate=False,
        stop_latch=False,
        radio_ready=True,
        manual_playback=False,
        planned_station_playing=True,
        bio_sleep=False,
        bio_state="awake",
        tv_power_on=False,
        audio_owner="homepods",
    )
    return replace(inp, **changes)


def test_live_timeline_keeps_owned_wake_repair_after_resume_signal_falls() -> None:
    """06:55 timeline: start permission falls only after playback is owned."""

    waking = _managed_inputs(
        action=C.ACTION_START_RADIO,
        homepods_resume_allowed=True,
        planned_station_playing=False,
        bio_state="waking",
    )
    assert L.playback_start_block_reason(
        waking, require_positive_target=False
    ) is None

    playing = replace(
        waking,
        action=C.ACTION_NONE,
        homepods_resume_allowed=False,
        planned_station_playing=True,
        bio_state="awake",
    )
    assert L.playback_repair_block_reason(
        playing, managed_episode=True
    ) is None

    health = L.playback_health(
        group_state="playing",
        pod_states=["playing", "playing"],
        pod_muted=[True, True],
        target=playing.homepods_target,
    )
    assert health == L.PlaybackHealth("unhealthy", "pod_1_muted")
    assert L.stuck_mute_targets(
        group_state="playing",
        entity_ids=PODS,
        pod_states=("playing", "playing"),
        pod_muted=(True, True),
    ) == PODS


def test_successful_wake_unmute_is_confirmed_after_one_call() -> None:
    muted = set(PODS)
    calls: list[tuple[str, ...]] = []

    async def unmute(targets: tuple[str, ...]) -> None:
        calls.append(targets)
        muted.difference_update(targets)

    async def run() -> L.UnmuteResult:
        return await L.bounded_unmute(
            PODS,
            unmute=unmute,
            remaining_muted=lambda: tuple(sorted(muted)),
            wait=lambda _delay: asyncio.sleep(0),
            block_reason=lambda: None,
        )

    result = asyncio.run(run())
    assert result == L.UnmuteResult("recovered", 1)
    assert calls == [PODS]


def test_failed_unmute_retries_are_strictly_bounded() -> None:
    calls: list[tuple[str, ...]] = []
    waits: list[float] = []

    async def unmute(targets: tuple[str, ...]) -> None:
        calls.append(targets)

    async def wait(delay: float) -> None:
        waits.append(delay)

    async def run() -> L.UnmuteResult:
        return await L.bounded_unmute(
            PODS,
            unmute=unmute,
            remaining_muted=lambda: PODS,
            wait=wait,
            block_reason=lambda: None,
            max_attempts=2,
            retry_delay=2.0,
        )

    result = asyncio.run(run())
    assert result == L.UnmuteResult("failed", 2, PODS, "mute_state_stuck")
    assert calls == [PODS, PODS]
    assert waits == [2.0, 2.0]


def test_unmute_service_failure_retries_once_then_reports_failure() -> None:
    calls = 0

    async def unmute(_targets: tuple[str, ...]) -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("provider unavailable")

    async def run() -> L.UnmuteResult:
        return await L.bounded_unmute(
            PODS,
            unmute=unmute,
            remaining_muted=lambda: PODS,
            wait=lambda _delay: asyncio.sleep(0),
            block_reason=lambda: None,
            max_attempts=2,
        )

    result = asyncio.run(run())
    assert result.state == "failed"
    assert result.attempts == 2
    assert result.reason == "unmute_service_failed:provider unavailable"
    assert calls == 2


def test_retry_rechecks_current_gate_before_second_side_effect() -> None:
    calls: list[tuple[str, ...]] = []
    blocked = False

    async def unmute(targets: tuple[str, ...]) -> None:
        nonlocal blocked
        calls.append(targets)
        blocked = True

    async def run() -> L.UnmuteResult:
        return await L.bounded_unmute(
            PODS,
            unmute=unmute,
            remaining_muted=lambda: PODS,
            wait=lambda _delay: asyncio.sleep(0),
            block_reason=lambda: "stop_latch" if blocked else None,
            max_attempts=2,
        )

    result = asyncio.run(run())
    assert result == L.UnmuteResult("cancelled", 1, PODS, "stop_latch")
    assert calls == [PODS]


def test_periodic_backstop_selects_only_evidenced_muted_playing_members() -> None:
    assert C.DEFAULT_STUCK_MUTE_BACKSTOP_INTERVAL == 30 * 60
    inp = _managed_inputs()
    assert L.playback_repair_block_reason(
        inp, managed_episode=inp.planned_station_playing is True
    ) is None
    assert L.stuck_mute_targets(
        group_state="playing",
        entity_ids=PODS,
        pod_states=("playing", "playing"),
        pod_muted=(False, True),
    ) == (PODS[1],)
    assert L.stuck_mute_targets(
        group_state="idle",
        entity_ids=PODS,
        pod_states=("playing", "playing"),
        pod_muted=(True, True),
    ) == ()


def test_backstop_path_has_no_play_start_or_volume_change_call() -> None:
    source = Path(
        "custom_components/benni_media_apply/coordinator.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    runner = functions["_run_stuck_mute_recovery"]
    called_attributes = {
        node.func.attr
        for node in ast.walk(runner)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "_unmute_homepods" in called_attributes
    assert called_attributes.isdisjoint(
        {"_dispatch_automatic_radio", "_schedule_radio_autostart", "_schedule_wake", "_run_ramp"}
    )


@pytest.mark.parametrize(
    ("changes", "managed", "reason"),
    [
        ({"quiet_mode": True}, True, "intentional_ducking"),
        ({"bio_state": "provisional_sleep"}, True, "sleep_context"),
        ({"bio_state": "sleep", "bio_sleep": True}, True, "sleep_context"),
        ({"stop_latch": True}, True, "stop_latch"),
        ({"action": C.ACTION_PAUSE}, True, "policy_pause"),
        ({"homepods_should_pause": True}, True, "policy_pause"),
        ({"volume_apply_allowed": False}, True, "volume_apply_not_allowed"),
        ({"homepods_target": 0.0}, True, "non_positive_target"),
        ({"audio_owner": "tv_denon"}, True, "competing_audio_owner"),
        ({"audio_owner": None}, True, "audio_owner_unproven"),
        ({"manual_playback": True}, True, "manual_playback"),
        ({"manual_playback": None}, True, "manual_playback_unproven"),
        ({}, False, "ownership_unproven"),
    ],
)
def test_repair_hard_abort_conditions(
    changes: dict[str, object], managed: bool, reason: str
) -> None:
    assert L.playback_repair_block_reason(
        _managed_inputs(**changes), managed_episode=managed
    ) == reason


def test_unknown_mute_state_is_not_reported_healthy() -> None:
    assert L.playback_health(
        group_state="playing",
        pod_states=["playing", "playing"],
        pod_muted=[False, None],
        target=0.30,
    ) == L.PlaybackHealth("unhealthy", "pod_2_mute_unknown")
