"""Binary sensor platform for the Dogtra Smart NoBark integration."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
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
    """Set up the Dogtra Smart NoBark connectivity binary sensor."""
    coordinator: DogtraDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([DogtraConnectivityBinarySensor(coordinator)])


class DogtraConnectivityBinarySensor(
    CoordinatorEntity[DogtraDataUpdateCoordinator], BinarySensorEntity
):
    """Binary sensor indicating whether the Dogtra collar is currently connected via BLE."""

    _attr_has_entity_name = True
    _attr_name = "Collar Connected"
    _attr_translation_key = "connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: DogtraDataUpdateCoordinator) -> None:
        """Initialize the connectivity binary sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_connected"

    @property
    def available(self) -> bool:
        """The connectivity sensor itself is always available."""
        return True

    @property
    def is_on(self) -> bool:
        """Return True if the collar is actively connected over BLE."""
        return self.coordinator.collar_state.connected

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
