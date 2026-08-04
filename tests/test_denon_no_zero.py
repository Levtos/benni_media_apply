"""benni_media#16 — den Denon NIE per volume_set auf 0 senken.

Belegter Live-Befund (Recorder, 2026-08-03 23:46, Musik→TV):

    23:46:08.678  living_denon on, vol 0.3   (feste HDMI-CEC-Einschaltlautstärke)
    23:46:08.759  living_denon vol 0         (apply: Kontext noch Musik → denon_target
                                              0.0, Ist 0.3 → _direct → volume_set(0))
    23:46:25      vol 0.2                     (erst nach Kontext=tv wieder hoch)
    ...           beim TV-Flacker erneut auf 0 → >45 s stumm

Die alte, funktionierende Logik (`media_orchestrator_volumes_v4`) setzte die
Denon-Lautstärke nur bei `dn_target > 0`. Ziel 0.0 heißt „Denon soll still/aus
sein" — KEIN `volume_set(0)`. Das physische Aus läuft über ACTION_DENON_OFF /
Nachlauf. Bei Ziel 0.0 bleibt der AVR auf seiner Einschaltlautstärke und wird
nur auf ein positives Ziel korrigiert (nie durch 0).
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


def _plan(inp, state=None):
    return L.decide_apply(inp, state)[0]


# --------------------------------------------------------------------------- #
# 1. Ziel 0.0 setzt den Denon NICHT auf 0 (Kern)
# --------------------------------------------------------------------------- #
def test_zero_target_does_not_set_denon_when_addressable():
    """Der belegte 23:46-Fall: Denon frisch an mit 0.3, Kontext noch Musik
    (Ziel 0.0) → apply lässt ihn in Ruhe, KEIN volume_set(0)."""
    p = _plan(_inp(denon_state="on", denon_volume=0.3, denon_target=0.0))
    assert p.denon_set is None
    assert "volume:denon_set" not in p.reasons


def test_zero_target_does_not_set_denon_watt_primary():
    """Auch im watt-primären Pfad (stale 'off', denon_power_on) kein 0-Set."""
    p = _plan(_inp(denon_state="off", denon_volume=None, denon_target=0.0,
                   denon_power_on=True))
    assert p.denon_set is None


def test_powered_on_denon_at_default_030_is_left_alone_until_tv_target():
    """AVR steht auf Einschaltlautstärke 0.3, Ziel noch 0.0 (Kontext-Lag) →
    nicht angefasst; erst ein positives Ziel korrigiert ihn."""
    idle = _plan(_inp(denon_state="on", denon_volume=0.3, denon_target=0.0))
    assert idle.denon_set is None
    tv = _plan(_inp(denon_state="on", denon_volume=0.3, denon_target=0.25))
    assert tv.denon_set == 0.25   # nur nach oben/unten aufs echte Ziel, nie via 0


# --------------------------------------------------------------------------- #
# 2. Positive Ziele bleiben unverändert (kein Regress)
# --------------------------------------------------------------------------- #
def test_positive_target_still_sets_denon():
    p = _plan(_inp(denon_state="on", denon_volume=0.3, denon_target=0.25))
    assert p.denon_set == 0.25


def test_small_positive_target_still_applies():
    """Ein niedriges, aber positives Ziel (z. B. Ducking) wird gesetzt."""
    p = _plan(_inp(denon_state="on", denon_volume=0.3, denon_target=0.05))
    assert p.denon_set == 0.05


def test_positive_target_watt_primary_still_sets():
    p = _plan(_inp(denon_state="off", denon_volume=None, denon_target=0.2,
                   denon_power_on=True))
    assert p.denon_set == 0.2


def test_idempotent_positive_target_no_double_write():
    """Ist == Soll (positiv) → kein erneuter Set (OSD-Flacker-Schutz bleibt)."""
    p = _plan(_inp(denon_state="on", denon_volume=0.25, denon_target=0.25))
    assert p.denon_set is None


# --------------------------------------------------------------------------- #
# 3. Physisches Aus bleibt getrennt (nicht über volume 0)
# --------------------------------------------------------------------------- #
def test_denon_off_goes_via_action_not_volume_zero():
    """Away schaltet den Denon per DENON_OFF ab, nicht per volume_set(0)."""
    p = _plan(_inp(away_gate=True, denon_state="on", denon_power_on=True))
    assert p.denon_action == C.ACTION_DENON_OFF
    assert p.denon_set is None
