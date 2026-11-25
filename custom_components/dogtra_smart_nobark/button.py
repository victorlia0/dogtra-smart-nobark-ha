"""Button platform for the Dogtra Smart NoBark integration."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import DogtraDataUpdateCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Dogtra Smart NoBark button entities."""
    coordinator: DogtraDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([DogtraResetSessionButton(coordinator)])


class DogtraResetSessionButton(
    CoordinatorEntity[DogtraDataUpdateCoordinator], ButtonEntity
):
    """Button entity to reset the Home Assistant session bark counter."""

    _attr_has_entity_name = True
    _attr_name = "Reset Session"
    _attr_translation_key = "reset_session"
    _attr_icon = "mdi:restart"

    def __init__(self, coordinator: DogtraDataUpdateCoordinator) -> None:
        """Initialize the reset session button."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_reset_session"

    @property
    def available(self) -> bool:
        """Always available so the session counter can be reset even when the collar is off."""
        return True

    async def async_press(self) -> None:
        """Reset the session bark count and send on-device session reset (0xB0) to the collar."""
        await self.coordinator.async_reset_session()

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
