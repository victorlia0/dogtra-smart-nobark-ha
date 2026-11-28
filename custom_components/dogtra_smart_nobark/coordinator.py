"""DataUpdateCoordinator for the Dogtra Smart NoBark integration."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from bleak.exc import BleakError

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .ble_client import DogtraBleClient
from .const import (
    COLLAR_MODE_NAME_TO_ID,
    CONF_BARK_BYTE_LENGTH,
    CONF_BARK_BYTE_OFFSET,
    CONF_BATTERY_BYTE_OFFSET,
    CONF_LITTLE_ENDIAN,
    CONF_MODE_BYTE_OFFSET,
    CONF_NOTIFY_CHAR_UUID,
    CONF_POLL_COMMAND_HEX,
    CONF_READ_CHAR_UUID,
    CONF_SCAN_INTERVAL,
    CONF_WHINE_BYTE_LENGTH,
    CONF_WHINE_BYTE_OFFSET,
    CONF_WRITE_CHAR_UUID,
    DEFAULT_BARK_BYTE_LENGTH,
    DEFAULT_BARK_BYTE_OFFSET,
    DEFAULT_BATTERY_BYTE_OFFSET,
    DEFAULT_LITTLE_ENDIAN,
    DEFAULT_MODE_BYTE_OFFSET,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_WHINE_BYTE_LENGTH,
    DEFAULT_WHINE_BYTE_OFFSET,
    DOMAIN,
    EVENT_BARK_DETECTED,
    SELECTABLE_COLLAR_MODES,
)
from .protocol import DogtraCollarState, DogtraProtocolDecoder, ParserConfig

_LOGGER = logging.getLogger(__name__)


class DogtraDataUpdateCoordinator(DataUpdateCoordinator[DogtraCollarState]):
    """Coordinator managing BLE advertisements, active connections, and state updates."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        address: str,
        name: str,
    ) -> None:
        """Initialize the Dogtra coordinator."""
        self.entry = entry
        self.address = address.upper()
        self.device_name = name

        scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{self.address}",
            update_interval=timedelta(seconds=scan_interval),
        )

        self.collar_state = DogtraCollarState(address=self.address, name=name)
        self.decoder = DogtraProtocolDecoder(self._build_parser_config(entry.options))
        self.ble_client = DogtraBleClient(
            state=self.collar_state,
            decoder=self.decoder,
            on_state_updated=self._handle_push_update,
            notify_char_uuid=entry.options.get(CONF_NOTIFY_CHAR_UUID),
            read_char_uuid=entry.options.get(CONF_READ_CHAR_UUID),
            write_char_uuid=entry.options.get(CONF_WRITE_CHAR_UUID),
            poll_command_hex=entry.options.get(CONF_POLL_COMMAND_HEX),
        )

    @staticmethod
    def _build_parser_config(options: dict[str, Any]) -> ParserConfig:
        """Create a ParserConfig from ConfigEntry options."""
        return ParserConfig(
            bark_byte_offset=int(
                options.get(CONF_BARK_BYTE_OFFSET, DEFAULT_BARK_BYTE_OFFSET)
            ),
            bark_byte_length=int(
                options.get(CONF_BARK_BYTE_LENGTH, DEFAULT_BARK_BYTE_LENGTH)
            ),
            whine_byte_offset=int(
                options.get(CONF_WHINE_BYTE_OFFSET, DEFAULT_WHINE_BYTE_OFFSET)
            ),
            whine_byte_length=int(
                options.get(CONF_WHINE_BYTE_LENGTH, DEFAULT_WHINE_BYTE_LENGTH)
            ),
            battery_byte_offset=int(
                options.get(CONF_BATTERY_BYTE_OFFSET, DEFAULT_BATTERY_BYTE_OFFSET)
            ),
            mode_byte_offset=int(
                options.get(CONF_MODE_BYTE_OFFSET, DEFAULT_MODE_BYTE_OFFSET)
            ),
            little_endian=bool(
                options.get(CONF_LITTLE_ENDIAN, DEFAULT_LITTLE_ENDIAN)
            ),
        )

    @callback
    def async_update_options(self, options: dict[str, Any]) -> None:
        """Apply updated ConfigEntry options without restarting Home Assistant."""
        self.decoder.update_config(self._build_parser_config(options))
        self.ble_client.notify_char_uuid = (
            options.get(CONF_NOTIFY_CHAR_UUID, "").strip().lower() or None
        )
        self.ble_client.read_char_uuid = (
            options.get(CONF_READ_CHAR_UUID, "").strip().lower() or None
        )
        self.ble_client.write_char_uuid = (
            options.get(CONF_WRITE_CHAR_UUID, "").strip().lower() or None
        )
        self.ble_client.poll_command_hex = (
            options.get(CONF_POLL_COMMAND_HEX, "").strip() or None
        )
        scan_interval = int(options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
        self.update_interval = timedelta(seconds=scan_interval)

    @callback
    def async_handle_bluetooth_event(
        self,
        service_info: bluetooth.BluetoothServiceInfoBleak,
        change: bluetooth.BluetoothChange,
    ) -> None:
        """Handle passive BLE advertisement packets from HA Bluetooth / ESPHome proxies."""
        self.collar_state.rssi = service_info.rssi
        if service_info.name and self.collar_state.name == "Dogtra Smart NoBark":
            self.collar_state.name = service_info.name

        if service_info.manufacturer_data:
            bark_delta = self.decoder.decode_manufacturer_data(
                service_info.manufacturer_data, self.collar_state
            )
            if bark_delta > 0:
                self._fire_bark_event(bark_delta)

        self.async_set_updated_data(self.collar_state)

        # If the collar just came into BLE range and we are not connected, trigger a poll
        if not self.ble_client.is_connected:
            self.hass.async_create_task(self.async_request_refresh())

    @callback
    def _handle_push_update(self, bark_delta: int) -> None:
        """Handle asynchronous push notifications or disconnect events from DogtraBleClient."""
        if bark_delta > 0:
            self._fire_bark_event(bark_delta)
        self.async_set_updated_data(self.collar_state)

    @callback
    def _fire_bark_event(self, bark_delta: int) -> None:
        """Fire an event on the Home Assistant bus when new barks are detected."""
        self.hass.bus.async_fire(
            EVENT_BARK_DETECTED,
            {
                "address": self.address,
                "name": self.collar_state.name,
                "bark_delta": bark_delta,
                "bark_count": self.collar_state.bark_count,
                "historic_bark_count": self.collar_state.historic_bark_count,
                "collar_mode": self.collar_state.collar_mode,
            },
        )

    async def _async_update_data(self) -> DogtraCollarState:
        """Poll the collar via Home Assistant's Bluetooth stack (local or ESPHome proxy)."""
        service_info = bluetooth.async_last_service_info(
            self.hass, self.address, connectable=True
        )
        if service_info is not None:
            self.collar_state.rssi = service_info.rssi

        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if ble_device is None:
            self.collar_state.connected = False
            # If we already have cached state from a previous session, keep showing it
            # while marking the collar disconnected instead of failing the entities.
            if self.collar_state.last_updated is not None:
                _LOGGER.debug(
                    "Dogtra collar %s is currently out of BLE range; keeping cached telemetry",
                    self.address,
                )
                return self.collar_state
            raise UpdateFailed(
                f"Dogtra Smart NoBark collar ({self.address}) is not currently in BLE range"
            )

        try:
            await self.ble_client.async_poll(ble_device)
        except (BleakError, TimeoutError, OSError) as err:
            self.collar_state.connected = False
            if self.collar_state.last_updated is not None:
                _LOGGER.debug(
                    "Transient BLE error polling %s (%s); keeping cached state",
                    self.address,
                    err,
                )
                return self.collar_state
            raise UpdateFailed(f"Error communicating with {self.address}: {err}") from err

        return self.collar_state

    async def async_reset_session(self) -> None:
        """Send on-device session reset (0xB0) to the collar over BLE."""
        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if ble_device is None:
            raise HomeAssistantError(
                f"Dogtra Smart NoBark collar ({self.address}) is currently out of BLE range"
            )

        try:
            await self.ble_client.async_reset_session(ble_device)
        except (BleakError, TimeoutError, OSError, ValueError) as err:
            raise HomeAssistantError(
                f"Failed to send reset command to collar: {err}"
            ) from err

        self.async_set_updated_data(self.collar_state)

    async def async_set_collar_mode(self, mode_name: str) -> None:
        """Set the collar's operating mode over BLE (non-stimulation modes only)."""
        if mode_name not in SELECTABLE_COLLAR_MODES:
            raise HomeAssistantError(
                f"Mode '{mode_name}' is disabled; only monitoring/non-stimulation modes ({', '.join(SELECTABLE_COLLAR_MODES)}) are permitted"
            )
        mode_id = COLLAR_MODE_NAME_TO_ID.get(mode_name)
        if mode_id is None:
            raise HomeAssistantError(f"Unsupported Dogtra collar mode: {mode_name}")

        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if ble_device is None:
            raise HomeAssistantError(
                f"Dogtra Smart NoBark collar ({self.address}) is currently out of BLE range"
            )

        try:
            await self.ble_client.async_set_mode(ble_device, mode_id)
        except (BleakError, TimeoutError, OSError, ValueError) as err:
            raise HomeAssistantError(
                f"Failed to set collar mode to {mode_name}: {err}"
            ) from err

        self.async_set_updated_data(self.collar_state)

    async def async_set_sensitivity(
        self,
        sound_level: int | None = None,
        vibration_level: int | None = None,
    ) -> None:
        """Update volume (audio) and/or vibration sensitivity on the collar over BLE."""
        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if ble_device is None:
            raise HomeAssistantError(
                f"Dogtra Smart NoBark collar ({self.address}) is currently out of BLE range"
            )

        try:
            await self.ble_client.async_set_sensitivity(
                ble_device,
                sound_level=sound_level,
                vibration_level=vibration_level,
            )
        except (BleakError, TimeoutError, OSError, ValueError) as err:
            raise HomeAssistantError(
                f"Failed to update collar sensitivity: {err}"
            ) from err

        self.async_set_updated_data(self.collar_state)

    async def async_shutdown(self) -> None:
        """Disconnect cleanly when unloading the config entry."""
        await super().async_shutdown()
        await self.ble_client.async_disconnect()
