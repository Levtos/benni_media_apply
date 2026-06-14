"""Panel-Registrierung für das Media-Apply-Dashboard.

Liefert das Vanilla-Web-Components-Frontend unter ``frontend/app/`` statisch aus
und registriert ein Custom-Panel in der HA-Sidebar (Muster wie benni_blind_policy).
Das Panel ist Wegwerf und konsumiert ausschließlich den WS-Contract.
"""
from __future__ import annotations

import logging
import os

from homeassistant.components.frontend import (
    async_register_built_in_panel,
    async_remove_panel,
)
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import (
    DATA_VIEW_PANEL,
    DATA_VIEW_STATIC,
    DOMAIN,
    FRONTEND_DIR_URL,
    FRONTEND_ENTRY,
    PANEL_ELEMENT,
    PANEL_ICON,
    PANEL_TITLE,
    PANEL_URL_PATH,
)

_LOGGER = logging.getLogger(__name__)

_BASE = os.path.dirname(__file__)
_APP_DIR = os.path.join(_BASE, "frontend", "app")


def _cache_bust() -> str:
    try:
        return str(int(os.path.getmtime(os.path.join(_APP_DIR, "main.js"))))
    except OSError:
        return "0"


async def async_setup_view(hass: HomeAssistant) -> None:
    data = hass.data.setdefault(DOMAIN, {})
    if not data.get(DATA_VIEW_STATIC):
        await hass.http.async_register_static_paths([
            StaticPathConfig(FRONTEND_DIR_URL, _APP_DIR, False),
        ])
        data[DATA_VIEW_STATIC] = True

    if data.get(DATA_VIEW_PANEL):
        return
    async_register_built_in_panel(
        hass,
        component_name="custom",
        sidebar_title=PANEL_TITLE,
        sidebar_icon=PANEL_ICON,
        frontend_url_path=PANEL_URL_PATH,
        require_admin=False,
        config={
            "_panel_custom": {
                "name": PANEL_ELEMENT,
                "module_url": f"{FRONTEND_ENTRY}?{_cache_bust()}",
            },
        },
    )
    data[DATA_VIEW_PANEL] = True


def async_remove_view(hass: HomeAssistant) -> None:
    data = hass.data.setdefault(DOMAIN, {})
    if not data.get(DATA_VIEW_PANEL):
        return
    try:
        async_remove_panel(hass, PANEL_URL_PATH)
    except Exception as err:  # noqa: BLE001 - Panel evtl. nie registriert
        _LOGGER.debug("panel remove skipped: %s", err)
    data[DATA_VIEW_PANEL] = False
