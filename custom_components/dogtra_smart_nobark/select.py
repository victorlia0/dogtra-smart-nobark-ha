"""Select platform for controlling the Dogtra Smart NoBark collar mode."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, SELECTABLE_COLLAR_MODES
from .coordinator import DogtraDataUpdateCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Dogtra Smart NoBark mode select entity."""
    coordinator: DogtraDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([DogtraCollarModeSelect(coordinator)])


class DogtraCollarModeSelect(
    CoordinatorEntity[DogtraDataUpdateCoordinator], SelectEntity
):
    """Select entity allowing users to change the Dogtra Smart NoBark collar mode."""

    _attr_has_entity_name = True
    _attr_name = "Collar Mode"
    _attr_translation_key = "collar_mode_select"
    _attr_icon = "mdi:tune-variant"
    _attr_options = SELECTABLE_COLLAR_MODES

    def __init__(self, coordinator: DogtraDataUpdateCoordinator) -> None:
        """Initialize the collar mode select entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_collar_mode_select"

    @property
    def available(self) -> bool:
        """Return True if collar is connected or has reported state."""
        state = self.coordinator.collar_state
        return state.connected or state.last_updated is not None

    @property
    def current_option(self) -> str | None:
        """Return the currently active collar mode."""
        mode = self.coordinator.collar_state.collar_mode
        if mode in self._attr_options:
            return mode
        return None

    async def async_select_option(self, option: str) -> None:
        """Change the collar's operating mode via BLE."""
        await self.coordinator.async_set_collar_mode(option)

    @property
    def device_info(self) -> DeviceInfo:
        """Return device registry information for this collar."""
        state = self.coordinator.collar_state
        return DeviceInfo(
            connections={(CONNECTION_BLUETOOTH, self.coordinator.address)},
            identifiers={(DOMAIN, self.coordinator.address)},
            name=state.name or self.coordinator.device_name,
            manufacturer=state.manufacturer or "Dogtra",
            model=state.model_number or "Smart NoBark",
            sw_version=state.firmware_version,
            hw_version=state.hardware_version,
        )
