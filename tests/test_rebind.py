"""ReBind guardrails for master-backed apply inputs."""
from __future__ import annotations

import bma_const as C


def test_power_inputs_default_to_masters():
    prefill = C.PROFILE_PREFILL[C.PROFILE_BENNI]

    assert prefill[C.CONF_PC_POWER] == "sensor.benni_master_pc"
    assert prefill[C.CONF_TV_POWER] == "sensor.benni_master_tv"


def test_legacy_power_inputs_have_master_repoints():
    assert C.LEGACY_ENTITY_MAP == {
        "sensor.benni_device_living_pc": "sensor.benni_master_pc",
        "sensor.benni_device_living_tv": "sensor.benni_master_tv",
        # FLEET-261: media presence/away_gate clean → system_ slug.
        "sensor.benni_media_state_presence_state": "sensor.system_benni_media_state_presence_state",
        "binary_sensor.benni_media_state_away_gate": "binary_sensor.system_benni_media_state_away_gate",
    }


def test_media_presence_and_away_gate_use_system_slugs():
    """FLEET-261: presence_state/away_gate binden auf die live system_-Slugs."""
    prefill = C.PROFILE_PREFILL[C.PROFILE_BENNI]

    # Default-PREFILL zeigt auf die live existierenden system_-Slugs.
    assert prefill[C.CONF_PRESENCE_STATE] == "sensor.system_benni_media_state_presence_state"
    assert prefill[C.CONF_AWAY_GATE] == "binary_sensor.system_benni_media_state_away_gate"

    # Legacy clean-Slugs werden auf die system_-Slugs repointet.
    assert (
        C.LEGACY_ENTITY_MAP["sensor.benni_media_state_presence_state"]
        == "sensor.system_benni_media_state_presence_state"
    )
    assert (
        C.LEGACY_ENTITY_MAP["binary_sensor.benni_media_state_away_gate"]
        == "binary_sensor.system_benni_media_state_away_gate"
    )

    # Domain bleibt korrekt (sensor bleibt sensor, binary_sensor bleibt binary_sensor).
    assert prefill[C.CONF_PRESENCE_STATE].startswith("sensor.")
    assert prefill[C.CONF_AWAY_GATE].startswith("binary_sensor.")
