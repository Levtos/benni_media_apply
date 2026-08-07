"""benni_media#16 — HomePods-Volume PRO POD statt auf die Gruppe.

Lastenheft (reviewed, `30_media/.../30_homepods_ramp_sync.yaml`): der Ramp
adressiert explizit die einzelnen AirPlay-Pods (`hp_*_airplay`), NIE die Gruppe —
„pro Gerät einzeln, kein Gruppen-Call". Grund (belegt, Live 2026-08-01 15:28):
ein `volume_set` auf die AirPlay-Sync-GRUPPE weckt einen pausierten Verbund wieder
auf; auf die einzelnen Pods nicht.

`volume_target_entities` liefert die Ziel-Entities für den Volume-Set/Ramp:
gebundene Pod-Liste bevorzugt, sonst Fallback auf die Gruppe (non-regressiv).
Pause/Resume/Radio bleiben auf der Gruppe (nicht hier getestet — nur der
Volume-Pfad ändert sich).
"""
from __future__ import annotations

import bma_const as C
import bma_logic as L


GROUP = "media_player.living_homepods_ma_group"
BLUE = "media_player.living_homepod_blue_ma_airplay"
GREY = "media_player.living_homepod_grey_ma_airplay"


# --------------------------------------------------------------------------- #
# 1. Ziel-Auswahl (reine Funktion)
# --------------------------------------------------------------------------- #
def test_pods_bound_target_the_pods_not_the_group():
    assert L.volume_target_entities([BLUE, GREY], GROUP) == [BLUE, GREY]


def test_two_pod_config_is_honored():
    """Black ist defekt/entfernt → 2-Pod-Konfig, kein Hardcode auf 3."""
    assert L.volume_target_entities([BLUE, GREY], GROUP) == [BLUE, GREY]
    assert GROUP not in L.volume_target_entities([BLUE, GREY], GROUP)


def test_empty_pods_fall_back_to_group():
    assert L.volume_target_entities([], GROUP) == [GROUP]
    assert L.volume_target_entities(None, GROUP) == [GROUP]


def test_pods_filter_non_strings_and_blanks():
    assert L.volume_target_entities([BLUE, "", None, GREY], GROUP) == [BLUE, GREY]


def test_no_pods_no_group_is_empty():
    assert L.volume_target_entities(None, None) == []
    assert L.volume_target_entities([], "") == []


def test_tuple_pods_supported():
    assert L.volume_target_entities((BLUE, GREY), GROUP) == [BLUE, GREY]


# --------------------------------------------------------------------------- #
# 2. Prefill: die einzelnen Pods sind gebunden, die Gruppe bleibt der Player
# --------------------------------------------------------------------------- #
def test_prefill_binds_individual_pods():
    prefill = C.PROFILE_PREFILL[C.DEFAULT_PROFILE]
    pods = prefill[C.CONF_HOMEPODS_PODS]
    assert pods == [BLUE, GREY]
    # Volume-Ziel = Pods, NICHT die Gruppe
    assert GROUP not in pods
    assert L.volume_target_entities(pods, prefill[C.CONF_HOMEPODS_PLAYER]) == [BLUE, GREY]


def test_group_still_bound_for_actions():
    """Pause/Resume/Radio-Ziel bleibt die Gruppe."""
    prefill = C.PROFILE_PREFILL[C.DEFAULT_PROFILE]
    assert prefill[C.CONF_HOMEPODS_PLAYER] == GROUP


# --------------------------------------------------------------------------- #
# 3. Pods sind Config-Slot, aber NICHT beobachtet (kein Ramp-Self-Trigger-Loop)
# --------------------------------------------------------------------------- #
def test_pods_are_a_config_slot():
    assert C.CONF_HOMEPODS_PODS in C.ENTITY_SLOT_KEYS


def test_pods_are_not_watched():
    """Ein State-Watch auf die Pods würde bei jedem Ramp-Schritt einen Recompute
    (Selbst-Trigger-Loop) auslösen — die Pods dürfen NICHT in WATCH_KEYS sein."""
    assert C.CONF_HOMEPODS_PODS not in C.WATCH_KEYS
