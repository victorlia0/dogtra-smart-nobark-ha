"""BLE GATT Client with automatic service/characteristic discovery for Dogtra Smart NoBark."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
from typing import Any

from bleak import BleakClient
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import establish_connection

from .const import (
    AUDIO_SENSITIVITY_LEVEL_TO_RAW,
    CANDIDATE_WRITE_CHAR_UUIDS,
    COLLAR_MODES,
    DOGTRA_INIT_COMMAND,
    DOGTRA_STATUS_PACKET_LEN,
    STANDARD_SERVICE_UUIDS,
    UUID_BATTERY_LEVEL_CHAR,
    UUID_DOGTRA_STATUS_CHAR,
    UUID_DOGTRA_WRITE_CHAR,
    UUID_FIRMWARE_REV_CHAR,
    UUID_HARDWARE_REV_CHAR,
    UUID_MANUFACTURER_NAME_CHAR,
    UUID_MODEL_NUMBER_CHAR,
)
from .protocol import (
    DogtraCollarState,
    DogtraProtocolDecoder,
    build_reset_session_command,
    build_set_mode_command,
    build_set_sensitivity_command,
)

_LOGGER = logging.getLogger(__name__)


class DogtraBleClient:
    """Manages BLE connection, GATT introspection, notifications, and polling."""

    def __init__(
        self,
        state: DogtraCollarState,
        decoder: DogtraProtocolDecoder,
        on_state_updated: Callable[[int], None] | None = None,
        notify_char_uuid: str | None = None,
        read_char_uuid: str | None = None,
        write_char_uuid: str | None = None,
        poll_command_hex: str | None = None,
    ) -> None:
        """Initialize the BLE client wrapper."""
        self.state = state
        self.decoder = decoder
        self._on_state_updated = on_state_updated
        self.notify_char_uuid = (notify_char_uuid or "").strip().lower() or None
        self.read_char_uuid = (read_char_uuid or "").strip().lower() or None
        self.write_char_uuid = (write_char_uuid or "").strip().lower() or None
        self.poll_command_hex = (poll_command_hex or "").strip() or None

        self._client: BleakClient | None = None
        self._connect_lock = asyncio.Lock()
        self._subscribed_chars: list[str] = []
        self._state_indication_event = asyncio.Event()

    @property
    def is_connected(self) -> bool:
        """Return True if currently connected to the BLE collar."""
        return bool(self._client and self._client.is_connected)

    async def async_poll(self, ble_device: BLEDevice) -> DogtraCollarState:
        """Ensure connection (or connect), read telemetry, and subscribe to notifications."""
        async with self._connect_lock:
            if not self.is_connected:
                await self._async_connect(ble_device)

            if self._client and self._client.is_connected:
                await self._async_read_telemetry(self._client)

        return self.state

    async def _async_read_fresh_state_frame(self, client: BleakClient) -> bytes:
        """Read a fresh 26-byte state frame from 0x2A59 or raise BleakError."""
        status_char = client.services.get_characteristic(UUID_DOGTRA_STATUS_CHAR)
        if not status_char or "read" not in status_char.properties:
            raise BleakError(
                f"Status characteristic {UUID_DOGTRA_STATUS_CHAR} is not readable on {self.state.address}"
            )

        raw = bytes(await client.read_gatt_char(status_char))
        if len(raw) < DOGTRA_STATUS_PACKET_LEN:
            raise BleakError(
                f"Expected at least {DOGTRA_STATUS_PACKET_LEN}-byte state frame from "
                f"{UUID_DOGTRA_STATUS_CHAR} on {self.state.address}, got {len(raw)} bytes"
            )

        self.decoder.decode_characteristic_payload(
            UUID_DOGTRA_STATUS_CHAR, raw, self.state
        )
        return bytes(raw[:DOGTRA_STATUS_PACKET_LEN])

    async def async_set_mode(
        self, ble_device: BLEDevice, mode_id: int
    ) -> DogtraCollarState:
        """Change the collar's operating mode by writing a Mode State packet to 0x2A58."""
        async with self._connect_lock:
            if not self.is_connected:
                await self._async_connect(ble_device)

            if not self._client or not self._client.is_connected:
                raise BleakError(f"Not connected to Dogtra collar {self.state.address}")

            client = self._client
            # Always read a fresh 26-byte state frame immediately before writing so
            # we never overwrite settings changed outside Home Assistant.
            fresh_frame = await self._async_read_fresh_state_frame(client)

            write_uuid = self.write_char_uuid or UUID_DOGTRA_WRITE_CHAR
            write_char = client.services.get_characteristic(write_uuid)
            if not write_char:
                raise BleakError(
                    f"Write characteristic {write_uuid} not found on {self.state.address}"
                )

            cmd = build_set_mode_command(mode_id, fresh_frame)
            response = "write" in write_char.properties
            _LOGGER.debug(
                "Writing set-mode command (mode_id=%s) to %s: %s",
                mode_id,
                write_char.uuid,
                cmd.hex(),
            )
            self._state_indication_event.clear()
            await client.write_gatt_char(write_char, cmd, response=response)

            # Optimistically update state immediately so Home Assistant UI reflects
            # the change right away instead of waiting for the next poll cycle.
            self.state.collar_mode_id = mode_id
            self.state.collar_mode = COLLAR_MODES.get(mode_id, "Count Only")
            patched = bytearray(fresh_frame)
            patched[1] = mode_id & 0xFF
            self.state.last_raw_state_bytes = bytes(patched)
            if self._on_state_updated:
                self._on_state_updated(0)

            # Wait briefly for the collar's 0x2A59 confirmation indication if it pushes one
            try:
                await asyncio.wait_for(self._state_indication_event.wait(), timeout=1.0)
            except TimeoutError:
                _LOGGER.debug(
                    "No immediate 0x2A59 indication after set-mode; keeping optimistic mode %s",
                    self.state.collar_mode,
                )

        return self.state

    async def async_set_sensitivity(
        self,
        ble_device: BLEDevice,
        sound_level: int | None = None,
        vibration_level: int | None = None,
    ) -> DogtraCollarState:
        """Update volume (audio) and/or vibration sensitivity by writing 0xB5 to 0x2A58."""
        async with self._connect_lock:
            if not self.is_connected:
                await self._async_connect(ble_device)

            if not self._client or not self._client.is_connected:
                raise BleakError(f"Not connected to Dogtra collar {self.state.address}")

            client = self._client
            # Always read a fresh 26-byte state frame immediately before writing
            fresh_frame = await self._async_read_fresh_state_frame(client)

            write_uuid = self.write_char_uuid or UUID_DOGTRA_WRITE_CHAR
            write_char = client.services.get_characteristic(write_uuid)
            if not write_char:
                raise BleakError(
                    f"Write characteristic {write_uuid} not found on {self.state.address}"
                )

            cmd = build_set_sensitivity_command(
                current_state_bytes=fresh_frame,
                sound_level=sound_level,
                vibration_level=vibration_level,
            )
            response = "write" in write_char.properties
            _LOGGER.debug(
                "Writing update-sensitivity command (0xB5, sound=%s, vib=%s) to %s: %s",
                sound_level,
                vibration_level,
                write_char.uuid,
                cmd.hex(),
            )
            self._state_indication_event.clear()
            await client.write_gatt_char(write_char, cmd, response=response)

            # Optimistically update state so Home Assistant sliders reflect the change immediately
            patched = bytearray(fresh_frame)
            if sound_level is not None:
                lvl = max(1, min(5, int(sound_level)))
                raw_sound = AUDIO_SENSITIVITY_LEVEL_TO_RAW[lvl]
                self.state.sound_sensitivity = lvl
                self.state.sound_sensitivity_raw = raw_sound
                patched[7] = raw_sound
            if vibration_level is not None:
                lvl = max(1, min(5, int(vibration_level)))
                self.state.vibration_sensitivity = lvl
                patched[8] = lvl
            self.state.last_raw_state_bytes = bytes(patched)
            if self._on_state_updated:
                self._on_state_updated(0)

            try:
                await asyncio.wait_for(self._state_indication_event.wait(), timeout=1.0)
            except TimeoutError:
                _LOGGER.debug(
                    "No immediate 0x2A59 indication after sensitivity update; keeping optimistic values"
                )

        return self.state

    async def async_reset_session(
        self, ble_device: BLEDevice
    ) -> DogtraCollarState:
        """Reset the collar's on-device wear time / session by writing 0xB0 to 0x2A58."""
        async with self._connect_lock:
            if not self.is_connected:
                await self._async_connect(ble_device)

            if not self._client or not self._client.is_connected:
                raise BleakError(f"Not connected to Dogtra collar {self.state.address}")

            client = self._client
            # Always read a fresh 26-byte state frame immediately before writing
            fresh_frame = await self._async_read_fresh_state_frame(client)

            write_uuid = self.write_char_uuid or UUID_DOGTRA_WRITE_CHAR
            write_char = client.services.get_characteristic(write_uuid)
            if not write_char:
                raise BleakError(
                    f"Write characteristic {write_uuid} not found on {self.state.address}"
                )

            cmd = build_reset_session_command(fresh_frame)
            response = "write" in write_char.properties
            _LOGGER.debug(
                "Writing reset-session command (0xB0) to %s: %s",
                write_char.uuid,
                cmd.hex(),
            )
            await client.write_gatt_char(write_char, cmd, response=response)

            await asyncio.sleep(0.3)
            status_char = client.services.get_characteristic(UUID_DOGTRA_STATUS_CHAR)
            if status_char and "read" in status_char.properties:
                raw = bytes(await client.read_gatt_char(status_char))
                self.decoder.decode_characteristic_payload(
                    UUID_DOGTRA_STATUS_CHAR, raw, self.state
                )

        return self.state

    async def _async_connect(self, ble_device: BLEDevice) -> None:
        """Establish a resilient BLE connection and inspect GATT services."""
        _LOGGER.debug(
            "Connecting to Dogtra Smart NoBark collar at %s (%s)",
            ble_device.address,
            ble_device.name,
        )
        try:
            client = await establish_connection(
                BleakClient,
                ble_device,
                self.state.name or ble_device.address,
                self._handle_disconnect,
                max_attempts=3,
            )
        except (BleakError, TimeoutError, OSError) as err:
            self.state.connected = False
            _LOGGER.debug("Failed to connect to %s: %s", ble_device.address, err)
            raise

        self._client = client
        self.state.connected = True
        self._subscribed_chars.clear()

        # Introspect GATT table for diagnostics and auto-discovery
        await self._async_introspect_gatt(client)

        # Read static device info once
        await self._async_read_device_info(client)

        # Subscribe to notify/indicate characteristics
        await self._async_subscribe_notifications(client)

        # Read fresh 26-byte collar state frame after every (re)connect
        try:
            await self._async_read_fresh_state_frame(client)
        except (BleakError, OSError) as err:
            _LOGGER.debug(
                "Initial 0x2A59 state frame read after connect failed on %s: %s",
                self.state.address,
                err,
            )

    def _handle_disconnect(self, _client: BleakClient) -> None:
        """Handle unexpected BLE disconnection."""
        _LOGGER.debug("Disconnected from Dogtra collar %s", self.state.address)
        self.state.connected = False
        self.state.last_raw_state_bytes = None
        self._client = None
        self._subscribed_chars.clear()
        if self._on_state_updated:
            self._on_state_updated(0)

    async def _async_introspect_gatt(self, client: BleakClient) -> None:
        """Catalog all GATT services and characteristics exposed by the collar."""
        profile: dict[str, Any] = {}
        for service in client.services:
            chars_info: list[dict[str, Any]] = []
            for char in service.characteristics:
                chars_info.append(
                    {
                        "uuid": char.uuid,
                        "handle": char.handle,
                        "description": char.description,
                        "properties": list(char.properties),
                    }
                )
            profile[service.uuid] = {
                "description": service.description,
                "characteristics": chars_info,
            }

        self.state.discovered_gatt_profile = profile
        _LOGGER.info(
            "Discovered GATT profile for %s: %s",
            self.state.address,
            profile,
        )

    async def _async_read_device_info(self, client: BleakClient) -> None:
        """Read standard Device Information Service characteristics if present."""
        info_map = [
            (UUID_MANUFACTURER_NAME_CHAR, "manufacturer"),
            (UUID_MODEL_NUMBER_CHAR, "model_number"),
            (UUID_FIRMWARE_REV_CHAR, "firmware_version"),
            (UUID_HARDWARE_REV_CHAR, "hardware_version"),
        ]
        for uuid_str, attr_name in info_map:
            char = client.services.get_characteristic(uuid_str)
            if char and "read" in char.properties:
                try:
                    raw = await client.read_gatt_char(char)
                    text = raw.decode("utf-8", errors="ignore").strip("\x00 ")
                    if text:
                        setattr(self.state, attr_name, text)
                except (BleakError, OSError) as err:
                    _LOGGER.debug("Could not read %s: %s", uuid_str, err)

    async def _async_subscribe_notifications(self, client: BleakClient) -> None:
        """Subscribe to configured or auto-discovered notify/indicate characteristics."""
        targets: list[BleakGATTCharacteristic] = []

        if self.notify_char_uuid:
            char = client.services.get_characteristic(self.notify_char_uuid)
            if char:
                targets.append(char)
            else:
                _LOGGER.warning(
                    "Configured notify characteristic %s not found on %s; falling back to auto-discovery",
                    self.notify_char_uuid,
                    self.state.address,
                )

        if not targets:
            # Auto-discover all custom notify/indicate characteristics + Battery Level
            for service in client.services:
                is_std_service = service.uuid.lower() in STANDARD_SERVICE_UUIDS
                for char in service.characteristics:
                    c_uuid = char.uuid.lower()
                    if is_std_service and c_uuid != UUID_BATTERY_LEVEL_CHAR:
                        continue
                    if "notify" in char.properties or "indicate" in char.properties:
                        targets.append(char)

        for char in targets:
            try:
                await client.start_notify(char, self._notification_handler)
                self._subscribed_chars.append(char.uuid)
                _LOGGER.info(
                    "Subscribed to BLE notifications on %s (%s)",
                    char.uuid,
                    char.description,
                )
            except (BleakError, OSError) as err:
                _LOGGER.debug(
                    "Could not subscribe to characteristic %s: %s", char.uuid, err
                )

    def _notification_handler(
        self, characteristic: BleakGATTCharacteristic, data: bytearray
    ) -> None:
        """Handle incoming BLE GATT notification/indication from the collar."""
        payload = bytes(data)
        _LOGGER.debug(
            "BLE Notification from %s [%s]: %s",
            self.state.address,
            characteristic.uuid,
            payload.hex(),
        )
        bark_delta = self.decoder.decode_characteristic_payload(
            characteristic.uuid, payload, self.state
        )
        if characteristic.uuid.lower() == UUID_DOGTRA_STATUS_CHAR:
            self._state_indication_event.set()
        if self._on_state_updated:
            self._on_state_updated(bark_delta)

    async def _async_read_telemetry(self, client: BleakClient) -> None:
        """Read battery level, custom readable characteristics, and send optional poll command."""
        # 1. Read standard Battery Level characteristic (0x2A19) if readable
        batt_char = client.services.get_characteristic(UUID_BATTERY_LEVEL_CHAR)
        if batt_char and "read" in batt_char.properties:
            try:
                raw_batt = await client.read_gatt_char(batt_char)
                self.decoder.decode_characteristic_payload(
                    UUID_BATTERY_LEVEL_CHAR, bytes(raw_batt), self.state
                )
            except (BleakError, OSError) as err:
                _LOGGER.debug("Battery read failed: %s", err)

        # 2. Send custom poll command or default Dogtra 0x2A58 Initialise/Sync command
        if self.poll_command_hex:
            await self._async_send_poll_command(client)
        else:
            write_char = client.services.get_characteristic(UUID_DOGTRA_WRITE_CHAR)
            if write_char and (
                "write" in write_char.properties
                or "write-without-response" in write_char.properties
            ):
                try:
                    await client.write_gatt_char(
                        write_char,
                        DOGTRA_INIT_COMMAND,
                        response="write" in write_char.properties,
                    )
                    await asyncio.sleep(0.2)
                except (BleakError, OSError) as err:
                    _LOGGER.debug("Dogtra init command write skipped: %s", err)

        # 3. Read explicit, verified Dogtra 0x2A59, or auto-discovered custom readable characteristics
        read_targets: list[BleakGATTCharacteristic] = []
        if self.read_char_uuid:
            char = client.services.get_characteristic(self.read_char_uuid)
            if char and "read" in char.properties:
                read_targets.append(char)
        else:
            dogtra_status = client.services.get_characteristic(UUID_DOGTRA_STATUS_CHAR)
            if dogtra_status and "read" in dogtra_status.properties:
                read_targets.append(dogtra_status)
            else:
                for service in client.services:
                    if service.uuid.lower() in STANDARD_SERVICE_UUIDS:
                        continue
                    for char in service.characteristics:
                        if "read" in char.properties:
                            read_targets.append(char)

        for char in read_targets:
            try:
                raw = await client.read_gatt_char(char)
                payload = bytes(raw)
                _LOGGER.debug(
                    "BLE Read from %s [%s]: %s",
                    self.state.address,
                    char.uuid,
                    payload.hex(),
                )
                bark_delta = self.decoder.decode_characteristic_payload(
                    char.uuid, payload, self.state
                )
                if bark_delta > 0 and self._on_state_updated:
                    self._on_state_updated(bark_delta)
            except (BleakError, OSError) as err:
                _LOGGER.debug("Read failed on %s: %s", char.uuid, err)

    async def _async_send_poll_command(self, client: BleakClient) -> None:
        """Write a hexadecimal poll command to the collar's write characteristic."""
        if not self.poll_command_hex:
            return
        try:
            cmd_bytes = bytes.fromhex(
                self.poll_command_hex.replace(" ", "").replace("0x", "")
            )
        except ValueError:
            _LOGGER.warning("Invalid poll_command_hex: %s", self.poll_command_hex)
            return

        target_char: BleakGATTCharacteristic | None = None
        if self.write_char_uuid:
            target_char = client.services.get_characteristic(self.write_char_uuid)

        if not target_char:
            for candidate_uuid in CANDIDATE_WRITE_CHAR_UUIDS:
                target_char = client.services.get_characteristic(candidate_uuid)
                if target_char:
                    break

        if not target_char:
            for service in client.services:
                if service.uuid.lower() in STANDARD_SERVICE_UUIDS:
                    continue
                for char in service.characteristics:
                    if (
                        "write" in char.properties
                        or "write-without-response" in char.properties
                    ):
                        target_char = char
                        break
                if target_char:
                    break

        if target_char:
            response = "write" in target_char.properties
            try:
                await client.write_gatt_char(target_char, cmd_bytes, response=response)
                _LOGGER.debug(
                    "Sent poll command %s to %s", cmd_bytes.hex(), target_char.uuid
                )
            except (BleakError, OSError) as err:
                _LOGGER.debug(
                    "Failed writing poll command to %s: %s", target_char.uuid, err
                )

    async def async_disconnect(self) -> None:
        """Disconnect cleanly from the collar."""
        async with self._connect_lock:
            if self._client and self._client.is_connected:
                for char_uuid in list(self._subscribed_chars):
                    try:
                        await self._client.stop_notify(char_uuid)
                    except (BleakError, OSError):
                        pass
                try:
                    await self._client.disconnect()
                except (BleakError, OSError):
                    pass
            self._client = None
            self.state.connected = False
            self.state.last_raw_state_bytes = None
            self._subscribed_chars.clear()
