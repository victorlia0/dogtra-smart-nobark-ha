"""Number platform for controlling Dogtra Smart NoBark sensitivities."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
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
    """Set up Dogtra Smart NoBark sensitivity number entities."""
    coordinator: DogtraDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            DogtraVolumeSensitivityNumber(coordinator),
            DogtraVibrationSensitivityNumber(coordinator),
        ]
    )


class _DogtraBaseSensitivityNumber(
    CoordinatorEntity[DogtraDataUpdateCoordinator], NumberEntity
):
    """Base class for Dogtra 1..5 sensitivity sliders."""

    _attr_has_entity_name = True
    _attr_native_min_value = 1.0
    _attr_native_max_value = 5.0
    _attr_native_step = 1.0
    _attr_mode = NumberMode.SLIDER

    @property
    def available(self) -> bool:
        """Return True if collar is connected or has reported state."""
        state = self.coordinator.collar_state
        return state.connected or state.last_updated is not None

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


class DogtraVolumeSensitivityNumber(_DogtraBaseSensitivityNumber):
    """Number slider for Dogtra Smart NoBark Volume / Audio Sensitivity (1–5)."""

    _attr_name = "Volume Sensitivity"
    _attr_translation_key = "volume_sensitivity"
    _attr_icon = "mdi:volume-high"

    def __init__(self, coordinator: DogtraDataUpdateCoordinator) -> None:
        """Initialize the volume sensitivity number entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_volume_sensitivity"

    @property
    def native_value(self) -> float | None:
        """Return the current volume sensitivity level (1..5)."""
        val = self.coordinator.collar_state.sound_sensitivity
        return float(val) if val is not None else None

    async def async_set_native_value(self, value: float) -> None:
        """Set the collar's volume sensitivity level over BLE."""
        await self.coordinator.async_set_sensitivity(sound_level=int(round(value)))


class DogtraVibrationSensitivityNumber(_DogtraBaseSensitivityNumber):
    """Number slider for Dogtra Smart NoBark Vibration / Motion Sensitivity (1–5)."""

    _attr_name = "Vibration Sensitivity"
    _attr_translation_key = "vibration_sensitivity"
    _attr_icon = "mdi:vibrate"

    def __init__(self, coordinator: DogtraDataUpdateCoordinator) -> None:
        """Initialize the vibration sensitivity number entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_vibration_sensitivity"

    @property
    def native_value(self) -> float | None:
        """Return the current vibration sensitivity level (1..5)."""
        val = self.coordinator.collar_state.vibration_sensitivity
        return float(val) if val is not None else None

    async def async_set_native_value(self, value: float) -> None:
        """Set the collar's vibration sensitivity level over BLE."""
        await self.coordinator.async_set_sensitivity(vibration_level=int(round(value)))
