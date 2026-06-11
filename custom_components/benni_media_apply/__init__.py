"""benni_media_apply — Ausführungsschicht / Executor (eigene HACS-Integration).

Single-Instance: ein Config-Entry (Profil benni/eltern) führt die media_policy-
Targets/Action idempotent + geramped an den echten Geräten aus. Konsumiert
benni_media_state + benni_media_policy über Entity-State (kein Import). Apply ist
gated (apply_enabled, Shadow-safe default OFF). Phase-1-Scaffold (kein Panel).
"""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DATA_COORDINATOR, DOMAIN
from .coordinator import MediaApplyCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coord = MediaApplyCoordinator(hass, entry)
    await coord.async_config_entry_first_refresh()
    coord.async_start()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {DATA_COORDINATOR: coord}

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    entry.async_on_unload(coord.async_shutdown_ramp)
    return True


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded
