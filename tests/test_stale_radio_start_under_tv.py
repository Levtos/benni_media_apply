"""benni_media#16 — kein (verzögerter) Musikstart unter aktivem TV.

Belegter Live-Folgefehler (Recorder, 2026-08-01, TV-Start ~22:17):

    22:17:22.844  TV-Master active          (Boot)
    22:17:34.844  TV-Master OFF             (externer Watt-Dip 43 W — Flacker)
    22:17:39.860  TV-Master active          (stabil, webOS `on`)
    22:17:54.869  manual_playback on→off    (Jack FM endete → Trigger B armt)
                  -> _schedule_radio_resume (RADIO_RESUME_DELAY 10 s)
    22:18:04..06  _run_radio_resume feuert  -> GAY.FM play_media (Default-Sender)
    22:18:10      Gruppe `playing` GAY.FM   ← Musik trotz laufendem TV
    ...           musste erneut pausiert werden (should_pause on/off-Flapping)

Zum Fire-Zeitpunkt war der TV STABIL aktiv (`audio_owner=tv_denon`,
`homepods_resume_allowed=off`). Trotzdem fingen weder `should_autostart_radio`
(TV-blind) noch die Coordinator-Bedingung `action==PAUSE` (Gruppe schon idle →
`action=none`) den Start ab.

Fix: `should_autostart_radio` (das gemeinsame Gate beim Planen UND beim
verzögerten Recheck) verlangt jetzt zusätzlich, dass der TV aus ist
(`screen_blocks_music_start` == `_tv_is_off(inp) is False`). Robust gegen den
kurzen TV-Master-Flacker, weil der STABILE Ist-Zustand zum Ausführungszeitpunkt
geprüft wird. `None` (TV unbekannt) blockt NICHT (non-regressiv).
"""
from __future__ import annotations

import bma_logic as L


def _ready(**kw):
    """Ein Inputs-Snapshot, der OHNE TV-Kontext autostarten würde."""
    base = dict(
        radio_ready=True,
        manual_playback=False,
        planned_station_playing=False,
    )
    base.update(kw)
    return L.Inputs(**base)


# --------------------------------------------------------------------------- #
# 1. Der reine Helper
# --------------------------------------------------------------------------- #
def test_screen_blocks_when_webos_on():
    assert L.screen_blocks_music_start(_ready(tv_player_state="on")) is True


def test_screen_blocks_when_webos_playing():
    assert L.screen_blocks_music_start(_ready(tv_player_state="playing")) is True


def test_screen_blocks_via_watt_fallback_when_webos_unknown():
    """WebOS ungebunden/unknown → Watt-Fallback: tv_power_on True = TV an."""
    assert L.screen_blocks_music_start(
        _ready(tv_player_state=None, tv_power_on=True)
    ) is True
    assert L.screen_blocks_music_start(
        _ready(tv_player_state="unavailable", tv_power_on=True)
    ) is True


def test_screen_does_not_block_when_tv_off():
    assert L.screen_blocks_music_start(
        _ready(tv_player_state="off", tv_power_on=False)
    ) is False


def test_screen_does_not_block_when_tv_unknown():
    """Non-regressiv: TV komplett unbekannt → kein Block."""
    assert L.screen_blocks_music_start(
        _ready(tv_player_state=None, tv_power_on=None)
    ) is False


# --------------------------------------------------------------------------- #
# 2. should_autostart_radio ist jetzt TV-bewusst (gemeinsames Gate)
# --------------------------------------------------------------------------- #
def test_autostart_blocked_while_tv_on_webos():
    assert L.should_autostart_radio(_ready(tv_player_state="on")) is False


def test_autostart_blocked_while_tv_on_watt_fallback():
    assert L.should_autostart_radio(
        _ready(tv_player_state=None, tv_power_on=True)
    ) is False


def test_autostart_allowed_while_tv_off():
    assert L.should_autostart_radio(
        _ready(tv_player_state="off", tv_power_on=False)
    ) is True


def test_autostart_allowed_while_tv_unknown_non_regressive():
    """Bestehendes Verhalten ohne TV-Bindung bleibt erhalten."""
    assert L.should_autostart_radio(
        _ready(tv_player_state=None, tv_power_on=None)
    ) is True


# --------------------------------------------------------------------------- #
# 3. Der EXAKTE belegte Fall: stabiler TV + Gruppe bereits idle (action != PAUSE)
# --------------------------------------------------------------------------- #
def test_delayed_resume_recheck_blocks_under_stable_tv_with_group_idle():
    """Genau die 22:18-Konstellation: TV stabil an, Gruppe idle, action `none`.
    Der verzögerte Resume ruft should_autostart_radio → muss jetzt abbrechen."""
    inp = _ready(
        tv_player_state="on",     # TV stabil aktiv
        tv_power_on=True,
        action=L.ACTION_NONE,     # Gruppe schon idle → nicht PAUSE (alter Guard griff nicht)
        homepods_state="idle",
    )
    assert L.should_autostart_radio(inp) is False


def test_flicker_settled_tv_on_blocks_even_after_false_music_window():
    """Nach dem Flacker (TV wieder stabil an) darf ein wartender Start nicht
    feuern — der Recheck sieht den stabilen Ist-Zustand."""
    settled = _ready(tv_player_state="on", tv_power_on=True)
    assert L.should_autostart_radio(settled) is False


# --------------------------------------------------------------------------- #
# 4. Andere Gates bleiben unberührt (TV aus, aber weiterhin korrekt blockiert)
# --------------------------------------------------------------------------- #
def test_other_gates_still_block_even_with_tv_off():
    off = dict(tv_player_state="off", tv_power_on=False)
    assert L.should_autostart_radio(_ready(radio_ready=False, **off)) is False
    assert L.should_autostart_radio(_ready(manual_playback=True, **off)) is False
    assert L.should_autostart_radio(_ready(planned_station_playing=True, **off)) is False
    assert L.should_autostart_radio(_ready(bio_sleep=True, **off)) is False
