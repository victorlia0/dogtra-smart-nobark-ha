"""Sensor platform for the Dogtra Smart NoBark integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    EntityCategory,
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import DogtraDataUpdateCoordinator
from .protocol import DogtraCollarState


@dataclass(frozen=True, kw_only=True)
class DogtraSensorEntityDescription(SensorEntityDescription):
    """Describes a Dogtra Smart NoBark sensor entity."""

    value_fn: Callable[[DogtraCollarState], int | str | datetime | None]
    extra_attrs_fn: Callable[[DogtraCollarState], dict[str, Any]] | None = None


SENSOR_DESCRIPTIONS: tuple[DogtraSensorEntityDescription, ...] = (
    DogtraSensorEntityDescription(
        key="bark_count",
        translation_key="bark_count",
        name="Bark Count",
        icon="mdi:dog-side",
        native_unit_of_measurement="barks",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda state: state.bark_count,
    ),
    DogtraSensorEntityDescription(
        key="whine_count",
        translation_key="whine_count",
        name="Whine & Howl Count",
        icon="mdi:dog",
        native_unit_of_measurement="events",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda state: (
            state.whine_and_howl_count
            if state.whine_and_howl_count is not None
            else state.whine_count
        ),
        extra_attrs_fn=lambda state: {
            "whine_count": state.whine_count,
            "howl_count": state.howl_count,
        },
    ),
    DogtraSensorEntityDescription(
        key="last_bark_time",
        translation_key="last_bark_time",
        name="Last Bark Detected",
        icon="mdi:clock-alert-outline",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda state: state.last_bark_time,
    ),
    DogtraSensorEntityDescription(
        key="battery_level",
        translation_key="battery_level",
        name="Battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda state: state.battery_level,
    ),
    DogtraSensorEntityDescription(
        key="collar_mode",
        translation_key="collar_mode",
        name="Collar Mode",
        icon="mdi:tune-variant",
        value_fn=lambda state: state.collar_mode,
        extra_attrs_fn=lambda state: {
            "mode_id": state.collar_mode_id,
            "stimulation_level": state.stimulation_level,
            "auto_increase_level": state.auto_increase_level,
            "auto_increase_min_level": state.auto_increase_min_level,
            "auto_increase_max_level": state.auto_increase_max_level,
            "sound_sensitivity": state.sound_sensitivity,
            "sound_sensitivity_raw": state.sound_sensitivity_raw,
            "vibration_sensitivity": state.vibration_sensitivity,
            "alarm": state.alarm,
            "individual_settings": state.individual_settings,
        },
    ),
    DogtraSensorEntityDescription(
        key="uptime_seconds",
        translation_key="uptime_seconds",
        name="Collar Wear Time",
        icon="mdi:timer-outline",
        value_fn=lambda state: state.uptime_formatted,
        extra_attrs_fn=lambda state: {"seconds": state.uptime_seconds},
    ),
    DogtraSensorEntityDescription(
        key="rssi",
        translation_key="rssi",
        name="Bluetooth Signal",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: state.rssi,
    ),
    DogtraSensorEntityDescription(
        key="last_raw_packet",
        translation_key="last_raw_packet",
        name="Last BLE Packet",
        icon="mdi:bluetooth-transfer",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda state: state.last_raw_packet_hex,
        extra_attrs_fn=lambda state: {
            "last_characteristic_uuid": state.last_char_uuid,
            "discovered_services": list(state.discovered_gatt_profile.keys()),
            "recent_packets": state.recent_packets[-5:],
        },
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Dogtra Smart NoBark sensors from a config entry."""
    coordinator: DogtraDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = [
        DogtraSensorEntity(coordinator, description)
        for description in SENSOR_DESCRIPTIONS
    ]
    entities.append(DogtraHistoricBarkSensor(coordinator))
    async_add_entities(entities)


class DogtraSensorEntity(
    CoordinatorEntity[DogtraDataUpdateCoordinator], SensorEntity
):
    """Representation of a Dogtra Smart NoBark sensor."""

    _attr_has_entity_name = True
    entity_description: DogtraSensorEntityDescription

    def __init__(
        self,
        coordinator: DogtraDataUpdateCoordinator,
        description: DogtraSensorEntityDescription,
    ) -> None:
        """Initialize the sensor entity."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.address}_{description.key}"

    @property
    def available(self) -> bool:
        """Return True if entity has data or collar is currently connected."""
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

    @property
    def native_value(self) -> int | str | datetime | None:
        """Return the current sensor value."""
        return self.entity_description.value_fn(self.coordinator.collar_state)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return optional extra state attributes."""
        if self.entity_description.extra_attrs_fn is not None:
            return self.entity_description.extra_attrs_fn(
                self.coordinator.collar_state
            )
        return None


class DogtraHistoricBarkSensor(
    CoordinatorEntity[DogtraDataUpdateCoordinator], RestoreSensor
):
    """Persistent lifetime bark counter that never resets across sessions or HA restarts."""

    _attr_has_entity_name = True
    _attr_name = "Historic Bark Count"
    _attr_translation_key = "historic_bark_count"
    _attr_icon = "mdi:history"
    _attr_native_unit_of_measurement = "barks"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, coordinator: DogtraDataUpdateCoordinator) -> None:
        """Initialize the persistent historic bark count sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_historic_bark_count"

    async def async_added_to_hass(self) -> None:
        """Restore the persisted lifetime bark count when Home Assistant starts."""
        await super().async_added_to_hass()
        last_data = await self.async_get_last_sensor_data()
        if last_data is not None and last_data.native_value is not None:
            try:
                restored = int(float(last_data.native_value))
                if restored > self.coordinator.collar_state.historic_bark_count:
                    self.coordinator.collar_state.historic_bark_count = restored
            except (TypeError, ValueError):
                pass

    @property
    def available(self) -> bool:
        """Always available since lifetime count is persisted in Home Assistant."""
        return True

    @property
    def native_value(self) -> int:
        """Return the cumulative lifetime bark count."""
        return self.coordinator.collar_state.historic_bark_count

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
