"""WebSocket-API für benni_media_apply.

Konsolidierter Apply-Status (Plan + Quellen + Gates + Geräte + Nachlauf +
Settings + Bindings) als das **Bleibende** (Umbrella-fähig), plus Apply-Gate-
Toggle. Read-Commands ohne Admin, Schreib-Commands nur Admin. Das Panel ist
Wegwerf und konsumiert ausschließlich diesen Contract.
"""
from __future__ import annotations

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant

from .const import (
    DATA_COORDINATOR, DOMAIN, WS_APPLY_REAPPLY_NOW, WS_CANCEL_REAPPLY,
    WS_GET_STATUS, WS_SCHEDULE_REAPPLY, WS_SET_APPLY_ENABLED,
)


def _coordinator(hass: HomeAssistant):
    for bucket in (hass.data.get(DOMAIN) or {}).values():
        if isinstance(bucket, dict) and DATA_COORDINATOR in bucket:
            return bucket[DATA_COORDINATOR]
    return None


def async_setup_websocket_api(hass: HomeAssistant) -> None:
    @websocket_api.websocket_command({vol.Required("type"): WS_GET_STATUS})
    @websocket_api.async_response
    async def ws_get_status(hass, connection, msg) -> None:
        coord = _coordinator(hass)
        if coord is None:
            connection.send_error(msg["id"], "not_ready", "Media Apply not loaded")
            return
        connection.send_result(msg["id"], coord.status())

    @websocket_api.websocket_command({
        vol.Required("type"): WS_SET_APPLY_ENABLED,
        vol.Required("enabled"): bool,
    })
    @websocket_api.require_admin
    @websocket_api.async_response
    async def ws_set_apply_enabled(hass, connection, msg) -> None:
        coord = _coordinator(hass)
        if coord is None:
            connection.send_error(msg["id"], "not_ready", "Media Apply not loaded")
            return
        await coord.async_set_apply_enabled(msg["enabled"])
        connection.send_result(msg["id"], coord.status())

    @websocket_api.websocket_command({
        vol.Required("type"): WS_SCHEDULE_REAPPLY,
        vol.Optional("delay_s", default=30): vol.All(vol.Coerce(float), vol.Range(min=0, max=300)),
        vol.Optional("reason"): str,
    })
    @websocket_api.require_admin
    @websocket_api.async_response
    async def ws_schedule_reapply(hass, connection, msg) -> None:
        coord = _coordinator(hass)
        if coord is None:
            connection.send_error(msg["id"], "not_ready", "Media Apply not loaded")
            return
        connection.send_result(msg["id"], coord.async_schedule_reapply(msg["delay_s"], msg.get("reason")))

    @websocket_api.websocket_command({vol.Required("type"): WS_APPLY_REAPPLY_NOW})
    @websocket_api.require_admin
    @websocket_api.async_response
    async def ws_apply_reapply_now(hass, connection, msg) -> None:
        coord = _coordinator(hass)
        if coord is None:
            connection.send_error(msg["id"], "not_ready", "Media Apply not loaded")
            return
        connection.send_result(msg["id"], coord.async_apply_reapply_now())

    @websocket_api.websocket_command({vol.Required("type"): WS_CANCEL_REAPPLY})
    @websocket_api.require_admin
    @websocket_api.async_response
    async def ws_cancel_reapply(hass, connection, msg) -> None:
        coord = _coordinator(hass)
        if coord is None:
            connection.send_error(msg["id"], "not_ready", "Media Apply not loaded")
            return
        connection.send_result(msg["id"], coord.async_cancel_reapply())

    for cmd in (ws_get_status, ws_set_apply_enabled, ws_schedule_reapply, ws_apply_reapply_now, ws_cancel_reapply):
        websocket_api.async_register_command(hass, cmd)
