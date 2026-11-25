"""Constants for the Dogtra Smart NoBark integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "dogtra_smart_nobark"

# Configuration keys
CONF_ADDRESS: Final = "address"
CONF_NAME: Final = "name"
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_SERVICE_UUID: Final = "service_uuid"
CONF_NOTIFY_CHAR_UUID: Final = "notify_char_uuid"
CONF_READ_CHAR_UUID: Final = "read_char_uuid"
CONF_WRITE_CHAR_UUID: Final = "write_char_uuid"
CONF_POLL_COMMAND_HEX: Final = "poll_command_hex"
CONF_BARK_BYTE_OFFSET: Final = "bark_byte_offset"
CONF_BARK_BYTE_LENGTH: Final = "bark_byte_length"
CONF_WHINE_BYTE_OFFSET: Final = "whine_byte_offset"
CONF_WHINE_BYTE_LENGTH: Final = "whine_byte_length"
CONF_BATTERY_BYTE_OFFSET: Final = "battery_byte_offset"
CONF_MODE_BYTE_OFFSET: Final = "mode_byte_offset"
CONF_LITTLE_ENDIAN: Final = "little_endian"

# Defaults
DEFAULT_NAME: Final = "Dogtra Smart NoBark"
DEFAULT_SCAN_INTERVAL: Final = 60  # seconds between active BLE status polls
DEFAULT_BARK_BYTE_OFFSET: Final = -1  # -1 means use heuristic / auto-detect parser
DEFAULT_BARK_BYTE_LENGTH: Final = 2
DEFAULT_WHINE_BYTE_OFFSET: Final = -1
DEFAULT_WHINE_BYTE_LENGTH: Final = 2
DEFAULT_BATTERY_BYTE_OFFSET: Final = -1
DEFAULT_MODE_BYTE_OFFSET: Final = -1
DEFAULT_LITTLE_ENDIAN: Final = True

# Standard Bluetooth SIG GATT UUIDs
UUID_GENERIC_ACCESS_SERVICE: Final = "00001800-0000-1000-8000-00805f9b34fb"
UUID_GENERIC_ATTRIBUTE_SERVICE: Final = "00001801-0000-1000-8000-00805f9b34fb"
UUID_DEVICE_INFO_SERVICE: Final = "0000180a-0000-1000-8000-00805f9b34fb"
UUID_BATTERY_SERVICE: Final = "0000180f-0000-1000-8000-00805f9b34fb"

UUID_BATTERY_LEVEL_CHAR: Final = "00002a19-0000-1000-8000-00805f9b34fb"
UUID_MANUFACTURER_NAME_CHAR: Final = "00002a29-0000-1000-8000-00805f9b34fb"
UUID_MODEL_NUMBER_CHAR: Final = "00002a24-0000-1000-8000-00805f9b34fb"
UUID_FIRMWARE_REV_CHAR: Final = "00002a26-0000-1000-8000-00805f9b34fb"
UUID_HARDWARE_REV_CHAR: Final = "00002a27-0000-1000-8000-00805f9b34fb"

STANDARD_SERVICE_UUIDS: Final[set[str]] = {
    UUID_GENERIC_ACCESS_SERVICE,
    UUID_GENERIC_ATTRIBUTE_SERVICE,
    UUID_DEVICE_INFO_SERVICE,
    UUID_BATTERY_SERVICE,
}

# Verified Dogtra Smart NoBark BLE GATT UUIDs (captured from DOGTRA_aff8)
UUID_DOGTRA_SERVICE: Final = "0000181a-0000-1000-8000-00805f9b34fb"
UUID_DOGTRA_WRITE_CHAR: Final = "00002a58-0000-1000-8000-00805f9b34fb"
UUID_DOGTRA_STATUS_CHAR: Final = "00002a59-0000-1000-8000-00805f9b34fb"
UUID_DOGTRA_AGGREGATE_CHAR: Final = "00002a5a-0000-1000-8000-00805f9b34fb"
UUID_DOGTRA_STRING_CHAR: Final = "00002a3d-0000-1000-8000-00805f9b34fb"

DOGTRA_STATUS_PACKET_LEN: Final = 26

# Initialise / Sync command written to 0x2A58 on connect to trigger a fresh 0x2A59 state push
DOGTRA_INIT_COMMAND: Final = bytes.fromhex(
    "A10000000000000000000000000000000000000000000000000001"
)

# Instructions for Byte 0 of 26-byte State packets written to 0x2A58
INSTRUCTION_SYNC: Final = 0x01
INSTRUCTION_SET_MODE: Final = 0x02
INSTRUCTION_RESET_DEVICE_TIME: Final = 0xB0
INSTRUCTION_UPDATE_SENSITIVITY: Final = 0xB5

# Audio / Volume sensitivity level (1..5) to raw byte value (Byte 7 of 0x2A59/0x2A58)
AUDIO_SENSITIVITY_LEVEL_TO_RAW: Final[dict[int, int]] = {
    1: 0x06,
    2: 0x0A,
    3: 0x0E,
    4: 0x12,
    5: 0x16,
}

# Candidate custom BLE UART / Vendor Service & Characteristic UUIDs
CANDIDATE_SERVICE_UUIDS: Final[list[str]] = [
    UUID_DOGTRA_SERVICE,
    "6e400001-b5a3-f393-e0a9-e50e24dcca9e",  # Nordic UART Service (NUS)
    "0000fff0-0000-1000-8000-00805f9b34fb",  # Custom FFF0 Vendor Service
    "0000ffe0-0000-1000-8000-00805f9b34fb",  # Custom FFE0 Vendor Service
]

CANDIDATE_NOTIFY_CHAR_UUIDS: Final[list[str]] = [
    UUID_DOGTRA_STATUS_CHAR,
    UUID_DOGTRA_AGGREGATE_CHAR,
    UUID_DOGTRA_STRING_CHAR,
    "6e400003-b5a3-f393-e0a9-e50e24dcca9e",
]

CANDIDATE_WRITE_CHAR_UUIDS: Final[list[str]] = [
    UUID_DOGTRA_WRITE_CHAR,
    UUID_DOGTRA_AGGREGATE_CHAR,
    UUID_DOGTRA_STRING_CHAR,
    "6e400002-b5a3-f393-e0a9-e50e24dcca9e",
]

# Collar operation modes reported by the Dogtra Smart NoBark (Byte 1 of 0x2A59)
COLLAR_MODES: Final[dict[int, str]] = {
    1: "Level",
    2: "Pager (Vibration)",
    3: "Auto Increase",
    5: "Count Only",
}

COLLAR_MODE_NAME_TO_ID: Final[dict[str, int]] = {
    name: mode_id for mode_id, name in COLLAR_MODES.items()
}

SELECTABLE_COLLAR_MODES: Final[list[str]] = [
    "Count Only",
    "Pager (Vibration)",
]

# Collar alarms reported in Byte 19 of 0x2A59
COLLAR_ALARMS: Final[dict[int, str]] = {
    0x00: "None",
    0x01: "Adjust Collar",
    0x02: "Increase Stimulation",
    0x03: "Increase Max Stimulation",
    0x04: "Try Stimulation Mode",
    0x0A: "Extended Wear Reminder (3 Hour)",
    0x0B: "Extended Wear Reminder (4 Hour)",
    0x0C: "Extended Wear Reminder (5 Hour)",
    0x0D: "Extended Wear Reminder (6 Hour)",
    0x0E: "Extended Wear Reminder (7 Hour)",
    0x0F: "Extended Wear Reminder (8 Hour)",
}

# Event fired on Home Assistant bus when bark count increases
EVENT_BARK_DETECTED: Final = f"{DOMAIN}_bark_detected"
