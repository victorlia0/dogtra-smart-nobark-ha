"""BLE Protocol parser and state models for Dogtra Smart NoBark."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
import re
from typing import Any

from .const import (
    AUDIO_SENSITIVITY_LEVEL_TO_RAW,
    COLLAR_ALARMS,
    COLLAR_MODES,
    DEFAULT_BARK_BYTE_LENGTH,
    DEFAULT_BARK_BYTE_OFFSET,
    DEFAULT_BATTERY_BYTE_OFFSET,
    DEFAULT_LITTLE_ENDIAN,
    DEFAULT_MODE_BYTE_OFFSET,
    DEFAULT_WHINE_BYTE_LENGTH,
    DEFAULT_WHINE_BYTE_OFFSET,
    DOGTRA_STATUS_PACKET_LEN,
    INSTRUCTION_RESET_DEVICE_TIME,
    INSTRUCTION_SET_MODE,
    INSTRUCTION_UPDATE_SENSITIVITY,
    UUID_BATTERY_LEVEL_CHAR,
    UUID_DOGTRA_STATUS_CHAR,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class ParserConfig:
    """Configuration for decoding Dogtra Smart NoBark BLE payloads."""

    bark_byte_offset: int = DEFAULT_BARK_BYTE_OFFSET
    bark_byte_length: int = DEFAULT_BARK_BYTE_LENGTH
    whine_byte_offset: int = DEFAULT_WHINE_BYTE_OFFSET
    whine_byte_length: int = DEFAULT_WHINE_BYTE_LENGTH
    battery_byte_offset: int = DEFAULT_BATTERY_BYTE_OFFSET
    mode_byte_offset: int = DEFAULT_MODE_BYTE_OFFSET
    little_endian: bool = DEFAULT_LITTLE_ENDIAN


@dataclass
class DogtraCollarState:
    """Represents the current state and telemetry of a Dogtra Smart NoBark collar."""

    address: str
    name: str = "Dogtra Smart NoBark"
    connected: bool = False
    rssi: int | None = None

    # Core telemetry
    bark_count: int | None = None
    howl_count: int | None = None
    whine_count: int | None = None
    whine_and_howl_count: int | None = None
    historic_bark_count: int = 0
    battery_level: int | None = None
    collar_mode_id: int | None = None
    collar_mode: str | None = None
    stimulation_level: int | None = None
    auto_increase_level: int | None = None
    auto_increase_min_level: int | None = None
    auto_increase_max_level: int | None = None
    sound_sensitivity: int | None = None
    sound_sensitivity_raw: int | None = None
    vibration_sensitivity: int | None = None
    individual_settings: dict[str, bool] = field(default_factory=dict)
    alarm_id: int | None = None
    alarm: str | None = None
    uptime_seconds: int | None = None
    uptime_formatted: str | None = None
    activity_count: int | None = None
    packet_type: int | None = None
    last_bark_time: datetime | None = None
    last_updated: datetime | None = None

    # Device metadata
    manufacturer: str = "Dogtra"
    model_number: str = "Smart NoBark"
    firmware_version: str | None = None
    hardware_version: str | None = None

    # Diagnostics & protocol introspection
    last_raw_state_bytes: bytes | None = None
    last_raw_packet_hex: str | None = None
    last_char_uuid: str | None = None
    discovered_gatt_profile: dict[str, Any] = field(default_factory=dict)
    recent_packets: list[dict[str, Any]] = field(default_factory=list)

    def record_packet(self, char_uuid: str, payload: bytes) -> None:
        """Record a raw BLE packet into diagnostic history (capped at 25 entries)."""
        hex_str = payload.hex()
        now = datetime.now(timezone.utc)
        self.last_raw_packet_hex = hex_str
        self.last_char_uuid = char_uuid
        self.last_updated = now
        self.recent_packets.append(
            {
                "timestamp": now.isoformat(),
                "characteristic": char_uuid,
                "hex": hex_str,
                "length": len(payload),
                "ascii": _safe_ascii(payload),
            }
        )
        if len(self.recent_packets) > 25:
            self.recent_packets = self.recent_packets[-25:]


def _safe_ascii(payload: bytes) -> str | None:
    """Return printable ASCII representation if payload is mostly printable text."""
    try:
        decoded = payload.decode("utf-8").strip()
        if decoded and all(32 <= ord(ch) < 127 or ch in "\r\n\t" for ch in decoded):
            return decoded
    except UnicodeDecodeError:
        pass
    return None


def _read_int(
    data: bytes, offset: int, length: int, little_endian: bool = True
) -> int | None:
    """Read an unsigned integer from a byte slice safely."""
    if offset < 0 or offset + length > len(data):
        return None
    byteorder = "little" if little_endian else "big"
    return int.from_bytes(data[offset : offset + length], byteorder=byteorder, signed=False)


class DogtraProtocolDecoder:
    """Decodes BLE GATT reads, notifications, and advertisement manufacturer data."""

    def __init__(self, config: ParserConfig | None = None) -> None:
        """Initialize the decoder with optional custom byte offsets."""
        self.config = config or ParserConfig()

    def update_config(self, config: ParserConfig) -> None:
        """Update parser configuration dynamically (e.g. from Options Flow)."""
        self.config = config

    def decode_characteristic_payload(
        self,
        char_uuid: str,
        payload: bytes,
        state: DogtraCollarState,
    ) -> int:
        """Decode a characteristic read/notification payload and update state in-place.

        Returns the positive bark delta (>= 0) if the bark count increased.
        """
        if not payload:
            return 0

        char_uuid_lower = char_uuid.lower()
        state.record_packet(char_uuid_lower, payload)

        # 1. Standard BLE Battery Level characteristic (0x2A19)
        if char_uuid_lower == UUID_BATTERY_LEVEL_CHAR or char_uuid_lower.startswith(
            "00002a19-"
        ):
            level = payload[0]
            if 0 <= level <= 100:
                state.battery_level = level
            return 0

        prev_barks = state.bark_count

        # 2. Check if user configured explicit byte offsets via Options Flow
        if self._has_explicit_offsets():
            self._apply_explicit_offsets(payload, state)
            return self._compute_bark_delta(prev_barks, state)

        # 3. Verified 26-byte Dogtra Smart NoBark status payload (00002a59)
        if (
            char_uuid_lower == UUID_DOGTRA_STATUS_CHAR
            or char_uuid_lower.startswith("00002a59-")
        ) and len(payload) >= DOGTRA_STATUS_PACKET_LEN:
            self._decode_dogtra_26b_status(payload, state)
            return self._compute_bark_delta(prev_barks, state)

        # 4. Try text / JSON / key-value protocol (e.g. over Nordic UART Service)
        ascii_text = _safe_ascii(payload)
        if ascii_text and self._try_decode_text_payload(ascii_text, state):
            return self._compute_bark_delta(prev_barks, state)

        # 5. Try binary framed / struct heuristic decoding
        self._try_decode_binary_payload(payload, state)
        return self._compute_bark_delta(prev_barks, state)

    def _decode_dogtra_26b_status(
        self, payload: bytes, state: DogtraCollarState
    ) -> None:
        """Decode the verified 26-byte Dogtra Smart NoBark payload from GATT 0x2A59.

        Full 26-byte State layout:
          [0]      Instruction (0x01=Sync, 0x02=Mode, 0x06=Alarm, 0x07=Bark Count Update)
          [1]      Mode (1=Level, 2=Pager, 3=Auto Increase, 5=Count Only)
          [2]      Shock / Stimulation Level
          [3]      Auto Increase Level
          [4]      Auto Increase Minimum Level
          [5]      Auto Increase Maximum Level
          [6]      Battery Level (0..100%)
          [7]      Audio / Volume Sensitivity (0x06=1, 0x0A=2, 0x0E=3, 0x12=4, 0x16=5)
          [8]      Vibration Sensitivity (1..5)
          [9..11]  Interval Bark / Howl / Whine counts
          [12..14] Interval Bark / Howl / Whine stimulate flags
          [15]     Individual Settings bitfield (0x10=LowVol, 0x20=Whine, 0x40=Howl, 0x80=Bark)
          [16..18] Collar wear/active time [hours, minutes, seconds]
          [19]     Alarm code (0x00=None, 0x01..0x0F)
          [20..21] Bark Total Count (uint16 BE)
          [22..23] Howl Total Count (uint16 BE)
          [24..25] Whine Total Count (uint16 BE)
        """
        state.last_raw_state_bytes = bytes(payload[:DOGTRA_STATUS_PACKET_LEN])
        state.packet_type = payload[0]

        mode_id = payload[1]
        state.collar_mode_id = mode_id
        state.collar_mode = COLLAR_MODES.get(mode_id, "Count Only")

        state.stimulation_level = payload[2]
        state.auto_increase_level = payload[3]
        state.auto_increase_min_level = payload[4]
        state.auto_increase_max_level = payload[5]

        batt = payload[6]
        if 0 <= batt <= 100:
            state.battery_level = batt

        sound_raw = payload[7]
        state.sound_sensitivity_raw = sound_raw
        if sound_raw >= 6 and (sound_raw - 2) % 4 == 0:
            state.sound_sensitivity = (sound_raw - 2) // 4
        else:
            state.sound_sensitivity = sound_raw

        state.vibration_sensitivity = payload[8]

        flags = payload[15]
        state.individual_settings = {
            "low_volume_detection": bool(flags & 0x10),
            "detect_whine": bool(flags & 0x20),
            "detect_howl": bool(flags & 0x40),
            "detect_bark": bool(flags & 0x80),
        }

        hours, minutes, seconds = payload[16], payload[17], payload[18]
        if minutes < 60 and seconds < 60:
            state.uptime_seconds = hours * 3600 + minutes * 60 + seconds
            state.uptime_formatted = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

        alarm_id = payload[19]
        state.alarm_id = alarm_id
        state.alarm = COLLAR_ALARMS.get(alarm_id, f"Alarm {alarm_id}")

        state.bark_count = int.from_bytes(payload[20:22], byteorder="big", signed=False)
        state.howl_count = int.from_bytes(payload[22:24], byteorder="big", signed=False)
        state.whine_count = int.from_bytes(payload[24:26], byteorder="big", signed=False)
        state.whine_and_howl_count = state.howl_count + state.whine_count
        state.activity_count = state.bark_count

    def decode_manufacturer_data(
        self,
        manufacturer_data: dict[int, bytes],
        state: DogtraCollarState,
    ) -> int:
        """Decode passive BLE advertisement manufacturer data if present."""
        if not manufacturer_data:
            return 0

        prev_barks = state.bark_count
        for company_id, data in manufacturer_data.items():
            state.record_packet(f"adv_mfg_0x{company_id:04x}", data)
            if self._has_explicit_offsets():
                self._apply_explicit_offsets(data, state)
            elif len(data) >= 4:
                # Only update if advertisement contains explicit framed data
                if data[0] in (0xAA, 0x55, 0xA5, 0xD0):
                    self._try_decode_binary_payload(data, state)
        return self._compute_bark_delta(prev_barks, state)

    def _has_explicit_offsets(self) -> bool:
        """Return True if any explicit byte offset (>= 0) is configured."""
        return any(
            offset >= 0
            for offset in (
                self.config.bark_byte_offset,
                self.config.whine_byte_offset,
                self.config.battery_byte_offset,
                self.config.mode_byte_offset,
            )
        )

    def _apply_explicit_offsets(
        self, payload: bytes, state: DogtraCollarState
    ) -> None:
        """Extract telemetry using user-configured byte offsets."""
        cfg = self.config
        if cfg.bark_byte_offset >= 0:
            val = _read_int(
                payload,
                cfg.bark_byte_offset,
                cfg.bark_byte_length,
                cfg.little_endian,
            )
            if val is not None:
                state.bark_count = val

        if cfg.whine_byte_offset >= 0:
            val = _read_int(
                payload,
                cfg.whine_byte_offset,
                cfg.whine_byte_length,
                cfg.little_endian,
            )
            if val is not None:
                state.whine_count = val

        if cfg.battery_byte_offset >= 0 and cfg.battery_byte_offset < len(payload):
            batt = payload[cfg.battery_byte_offset]
            if 0 <= batt <= 100:
                state.battery_level = batt

        if cfg.mode_byte_offset >= 0 and cfg.mode_byte_offset < len(payload):
            mode_id = payload[cfg.mode_byte_offset]
            state.collar_mode_id = mode_id
            state.collar_mode = COLLAR_MODES.get(mode_id, f"Mode {mode_id}")

    def _try_decode_text_payload(
        self, text: str, state: DogtraCollarState
    ) -> bool:
        """Decode ASCII / JSON / AT-command style payloads if used by the collar."""
        # JSON payload e.g. {"bark": 12, "whine": 3, "bat": 85, "mode": 1}
        if text.startswith("{") and text.endswith("}"):
            try:
                obj = json.loads(text)
                if isinstance(obj, dict):
                    for k in ("bark", "barks", "bark_count", "barkCount", "cnt"):
                        if k in obj and isinstance(obj[k], int):
                            state.bark_count = obj[k]
                    for k in ("whine", "whines", "whine_count", "howl"):
                        if k in obj and isinstance(obj[k], int):
                            state.whine_count = obj[k]
                    for k in ("bat", "batt", "battery", "soc"):
                        if k in obj and isinstance(obj[k], int):
                            state.battery_level = max(0, min(100, obj[k]))
                    for k in ("mode", "level", "stim"):
                        if k in obj and isinstance(obj[k], int):
                            state.collar_mode_id = obj[k]
                            state.collar_mode = COLLAR_MODES.get(
                                obj[k], f"Mode {obj[k]}"
                            )
                    return True
            except ValueError:
                pass

        # Key-value / AT style e.g. "BARK:15,WHINE:2,BAT:90,MODE:1"
        matched = False
        bark_m = re.search(r"(?:bark|cnt|b)\s*[:=]\s*(\d+)", text, re.IGNORECASE)
        if bark_m:
            state.bark_count = int(bark_m.group(1))
            matched = True

        whine_m = re.search(r"(?:whine|howl|w)\s*[:=]\s*(\d+)", text, re.IGNORECASE)
        if whine_m:
            state.whine_count = int(whine_m.group(1))
            matched = True

        bat_m = re.search(r"(?:bat|batt|battery)\s*[:=]\s*(\d+)", text, re.IGNORECASE)
        if bat_m:
            batt = int(bat_m.group(1))
            if 0 <= batt <= 100:
                state.battery_level = batt
                matched = True

        mode_m = re.search(r"(?:mode|lvl|level)\s*[:=]\s*(\d+)", text, re.IGNORECASE)
        if mode_m:
            mode_id = int(mode_m.group(1))
            state.collar_mode_id = mode_id
            state.collar_mode = COLLAR_MODES.get(mode_id, f"Mode {mode_id}")
            matched = True

        return matched

    def _try_decode_binary_payload(
        self, payload: bytes, state: DogtraCollarState
    ) -> None:
        """Decode binary BLE packets from Dogtra Smart NoBark.

        Supports common binary packet layouts:
        - 1-byte payload: Battery level (0..100) or Mode
        - 2-byte payload: uint16 bark count (little-endian default)
        - 4-byte payload: [uint16 bark_count, uint16 whine_count]
        - Framed packet starting with header (0xAA, 0x55, 0xA5, 0xFE, 0xD0):
          e.g. [SOF, CMD/LEN, MODE, BATTERY, BARK_L, BARK_H, WHINE_L, WHINE_H, CRC]
        - Unframed status struct (5..20 bytes):
          e.g. [MODE, BATTERY, BARK_L, BARK_H, WHINE_L, WHINE_H, ...]
        """
        length = len(payload)
        le = self.config.little_endian

        if length == 1:
            val = payload[0]
            if 0 <= val <= 100 and state.battery_level is None:
                state.battery_level = val
            return

        if length == 2:
            state.bark_count = _read_int(payload, 0, 2, le)
            return

        if length == 4:
            state.bark_count = _read_int(payload, 0, 2, le)
            state.whine_count = _read_int(payload, 2, 2, le)
            return

        # Framed packet starting with standard start-of-frame bytes
        if payload[0] in (0xAA, 0x55, 0xA5, 0xFE, 0xD0) and length >= 6:
            # Strip SOF (1 or 2 bytes if 0xAA 0x55)
            hdr_len = 2 if (payload[0] == 0xAA and payload[1] == 0x55) else 1
            body = payload[hdr_len:]
            # Common layout: [cmd, mode, battery, bark_lo, bark_hi, (whine_lo, whine_hi)]
            if len(body) >= 5:
                cmd_or_mode = body[1] if len(body) >= 6 else body[0]
                batt_candidate = body[2] if len(body) >= 6 else body[1]
                bark_offset = 3 if len(body) >= 6 else 2

                if cmd_or_mode in COLLAR_MODES:
                    state.collar_mode_id = cmd_or_mode
                    state.collar_mode = COLLAR_MODES[cmd_or_mode]

                if 0 < batt_candidate <= 100:
                    state.battery_level = batt_candidate
                elif 1 <= batt_candidate <= 4 and state.battery_level is None:
                    state.battery_level = batt_candidate * 25

                bark_val = _read_int(body, bark_offset, 2, le)
                if bark_val is not None:
                    state.bark_count = bark_val

                if len(body) >= bark_offset + 4:
                    whine_val = _read_int(body, bark_offset + 2, 2, le)
                    if whine_val is not None:
                        state.whine_count = whine_val
                return

        # Unframed status struct (>= 5 bytes)
        if length >= 5:
            # Check if bytes 0..1 are mode + battery (0..12, 1..100)
            if payload[0] in COLLAR_MODES and 0 < payload[1] <= 100:
                state.collar_mode_id = payload[0]
                state.collar_mode = COLLAR_MODES[payload[0]]
                state.battery_level = payload[1]
                state.bark_count = _read_int(payload, 2, 2, le)
                if length >= 6:
                    state.whine_count = _read_int(payload, 4, 2, le)
            else:
                # Fallback: first uint16 is bark_count, second uint16 is whine_count
                state.bark_count = _read_int(payload, 0, 2, le)
                state.whine_count = _read_int(payload, 2, 2, le)
                if 0 < payload[4] <= 100:
                    state.battery_level = payload[4]

    def _compute_bark_delta(
        self, prev_barks: int | None, state: DogtraCollarState
    ) -> int:
        """Update session & historic bark counters and timestamps if bark_count increased."""
        current = state.bark_count
        if current is None:
            return 0
        if prev_barks is None:
            # On very first read (no restored historic value yet), seed historic count
            if state.historic_bark_count == 0 and current > 0:
                state.historic_bark_count = current
            return 0
        if current > prev_barks:
            delta = current - prev_barks
            state.historic_bark_count += delta
            state.last_bark_time = datetime.now(timezone.utc)
            return delta
        if 0 < current < prev_barks:
            # Collar counter was reset while offline and has since recorded `current` new barks
            delta = current
            state.historic_bark_count += delta
            state.last_bark_time = datetime.now(timezone.utc)
            return delta
        return 0


def build_set_mode_command(
    mode_id: int, current_state_bytes: bytes | None = None
) -> bytes:
    """Build a 26-byte Mode update packet for GATT 0x2A58.

    Preserves all existing collar settings (levels, sensitivities, flags) from
    the most recent 0x2A59 state packet while setting Byte 0 = 0x02 (Mode) and
    Byte 1 = mode_id.
    """
    if current_state_bytes and len(current_state_bytes) >= DOGTRA_STATUS_PACKET_LEN:
        buf = bytearray(current_state_bytes[:DOGTRA_STATUS_PACKET_LEN])
    else:
        buf = bytearray(DOGTRA_STATUS_PACKET_LEN)

    buf[0] = INSTRUCTION_SET_MODE
    buf[1] = mode_id & 0xFF
    return bytes(buf)


def build_reset_session_command(
    current_state_bytes: bytes | None = None,
) -> bytes:
    """Build a 26-byte Reset Device Time / Session packet (0xB0) for GATT 0x2A58.

    Preserves all collar settings (bytes 1..15) while setting Byte 0 = 0xB0
    and clearing the active time & session totals (bytes 16..25).
    """
    if current_state_bytes and len(current_state_bytes) >= DOGTRA_STATUS_PACKET_LEN:
        buf = bytearray(current_state_bytes[:DOGTRA_STATUS_PACKET_LEN])
    else:
        buf = bytearray(DOGTRA_STATUS_PACKET_LEN)

    buf[0] = INSTRUCTION_RESET_DEVICE_TIME
    for i in range(16, DOGTRA_STATUS_PACKET_LEN):
        buf[i] = 0
    return bytes(buf)


def build_set_sensitivity_command(
    sound_level: int | None = None,
    vibration_level: int | None = None,
    current_state_bytes: bytes | None = None,
) -> bytes:
    """Build a 26-byte Update Sensitivity packet (0xB5) for GATT 0x2A58.

    Preserves all existing collar settings while updating Byte 7 (Volume/Audio
    Sensitivity, 1..5 -> 0x06..0x16) and/or Byte 8 (Vibration Sensitivity, 1..5).
    """
    if current_state_bytes and len(current_state_bytes) >= DOGTRA_STATUS_PACKET_LEN:
        buf = bytearray(current_state_bytes[:DOGTRA_STATUS_PACKET_LEN])
    else:
        buf = bytearray(DOGTRA_STATUS_PACKET_LEN)

    buf[0] = INSTRUCTION_UPDATE_SENSITIVITY
    if sound_level is not None:
        clamped_sound = max(1, min(5, int(sound_level)))
        buf[7] = AUDIO_SENSITIVITY_LEVEL_TO_RAW[clamped_sound]
    if vibration_level is not None:
        clamped_vib = max(1, min(5, int(vibration_level)))
        buf[8] = clamped_vib
    return bytes(buf)

