"""Diagnostics support for Dogtra Smart NoBark."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import DogtraDataUpdateCoordinator

TO_REDACT = {CONF_ADDRESS}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry, including full GATT introspection and packet logs."""
    coordinator: DogtraDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    state = coordinator.collar_state

    return {
        "entry_data": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        "collar_state": {
            "connected": state.connected,
            "rssi": state.rssi,
            "bark_count": state.bark_count,
            "whine_count": state.whine_count,
            "historic_bark_count": state.historic_bark_count,
            "battery_level": state.battery_level,
            "collar_mode_id": state.collar_mode_id,
            "collar_mode": state.collar_mode,
            "last_bark_time": (
                state.last_bark_time.isoformat() if state.last_bark_time else None
            ),
            "last_updated": (
                state.last_updated.isoformat() if state.last_updated else None
            ),
            "manufacturer": state.manufacturer,
            "model_number": state.model_number,
            "firmware_version": state.firmware_version,
            "hardware_version": state.hardware_version,
        },
        "discovered_gatt_profile": state.discovered_gatt_profile,
        "recent_ble_packets": state.recent_packets,
    }
