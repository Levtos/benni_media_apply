"""Entity-Basis + Status-/Debug-Roster für benni_media_apply.

Beschreibungs-getrieben: das Roster lebt hier als Daten (SENSORS /
BINARY_SENSORS); sensor.py / binary_sensor.py bauen daraus die Entities. Alle
lesen aus `coordinator.data` (= der Apply-Plan, im Shadow geplant nicht geführt).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_PROFILE,
    DEFAULT_PROFILE,
    DOMAIN,
    PROFILE_LABELS,
    UID_APPLY_ENABLED,
    UID_DENON_TARGET,
    UID_EXECUTE,
    UID_HOMEPODS_TARGET,
    UID_LAST_ACTION,
    UID_RAMP_ACTIVE,
    unique_id,
)
from .coordinator import MediaApplyCoordinator


@dataclass(frozen=True)
class FieldDesc:
    key: str            # Feld in coordinator.data
    uid: str            # unique_id-Suffix (auch object_id-Basis)
    name: str           # friendly name
    icon: str | None = None
    unit: str | None = None


SENSORS: tuple[FieldDesc, ...] = (
    FieldDesc("last_action", UID_LAST_ACTION, "Last Action", "mdi:play-pause"),
    FieldDesc("homepods_target", UID_HOMEPODS_TARGET, "HomePods Target", "mdi:speaker", "%"),
    FieldDesc("denon_target", UID_DENON_TARGET, "Denon Target", "mdi:audio-video", "%"),
)

BINARY_SENSORS: tuple[FieldDesc, ...] = (
    FieldDesc("ramp_active", UID_RAMP_ACTIVE, "Ramp Active", "mdi:transfer"),
    FieldDesc("apply_enabled", UID_APPLY_ENABLED, "Apply Enabled (Live)", "mdi:lock-open-check"),
    FieldDesc("execute", UID_EXECUTE, "Execute", "mdi:flash"),
)


def device_info(entry: ConfigEntry) -> dict[str, Any]:
    # Der Device-Name bestimmt bei has_entity_name den Entity-Slug:
    #   "Benni Media Apply"  → sensor.benni_media_apply_*
    #   "Eltern Media Apply" → sensor.eltern_media_apply_*
    profile = entry.data.get(CONF_PROFILE, DEFAULT_PROFILE)
    label = PROFILE_LABELS.get(profile, "Benni")
    return {
        "identifiers": {(DOMAIN, entry.entry_id)},
        "name": f"{label} Media Apply",
        "manufacturer": "Benni",
        "model": f"Media Apply · {label}",
    }


class MediaApplyEntity(CoordinatorEntity[MediaApplyCoordinator]):
    """Gemeinsame Basis: liest aus coordinator.data via FieldDesc."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: MediaApplyCoordinator, entry: ConfigEntry, desc: FieldDesc
    ) -> None:
        super().__init__(coordinator)
        self._desc = desc
        self._attr_unique_id = unique_id(entry.entry_id, desc.uid)
        self._attr_name = desc.name
        if desc.icon:
            self._attr_icon = desc.icon
        self._attr_device_info = device_info(entry)

    @property
    def _value(self) -> Any:
        return (self.coordinator.data or {}).get(self._desc.key)
