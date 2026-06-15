"""Switch-Plattform: Apply-Arm (Kill-Switch).

Ein einziger Schalter, der `apply_enabled` (Option) führt — der Arm/Not-Aus
für die gesamte Media-Apply-Schicht. Spiegelt das Pattern aus benni_blind_policy
(switch.<profile>_blind_policy_apply_enabled), damit das Scharfschalten und der
Not-Aus von jedem Dashboard/Cockpit aus erreichbar sind statt nur über die
vergrabene Integrations-Option.

Shadow-safe: Default OFF. ON = plant *und* führt (gated durch
volume_apply_allowed der Policy). OFF = reiner Shadow (plant, führt nichts).
"""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DATA_COORDINATOR, DOMAIN, unique_id
from .coordinator import MediaApplyCoordinator
from .entities import device_info


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coord = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    async_add_entities([MediaApplyArmSwitch(coord, entry)])


class MediaApplyArmSwitch(CoordinatorEntity[MediaApplyCoordinator], SwitchEntity):
    """Arm/Not-Aus für die Media-Apply-Schicht (führt apply_enabled)."""

    _attr_has_entity_name = True
    _attr_name = "Automatik scharf"
    _attr_icon = "mdi:lock-open-check"

    def __init__(self, coordinator: MediaApplyCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = unique_id(entry.entry_id, "apply_arm_switch")
        self._attr_device_info = device_info(entry)

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.apply_enabled)

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_set_apply_enabled(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_set_apply_enabled(False)
        self.async_write_ha_state()
