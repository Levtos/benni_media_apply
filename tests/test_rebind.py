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
    }
