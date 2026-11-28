"""Unit tests for Dogtra Smart NoBark BLE protocol decoding."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
import unittest

# Create a lightweight namespace package for dogtra_smart_nobark so tests run
# without requiring the full Home Assistant core environment installed locally.
COMP_DIR = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "dogtra_smart_nobark"
)
pkg = types.ModuleType("dogtra_smart_nobark")
pkg.__path__ = [str(COMP_DIR)]
sys.modules["dogtra_smart_nobark"] = pkg

for mod_name in ("const", "protocol"):
    spec = importlib.util.spec_from_file_location(
        f"dogtra_smart_nobark.{mod_name}", COMP_DIR / f"{mod_name}.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"dogtra_smart_nobark.{mod_name}"] = mod
    spec.loader.exec_module(mod)

from dogtra_smart_nobark.const import (  # noqa: E402
    UUID_BATTERY_LEVEL_CHAR,
    UUID_DOGTRA_STATUS_CHAR,
)
from dogtra_smart_nobark.protocol import (  # noqa: E402
    DogtraCollarState,
    DogtraProtocolDecoder,
    ParserConfig,
    build_reset_session_command,
    build_set_mode_command,
    build_set_sensitivity_command,
)


class TestDogtraProtocolDecoder(unittest.TestCase):
    """Test cases for DogtraProtocolDecoder."""

    def test_live_captured_dogtra_26b_status_packets(self) -> None:
        state = DogtraCollarState(address="FD:23:3C:AF:AF:F8", name="DOGTRA_aff8")
        decoder = DogtraProtocolDecoder()

        # Real 26-byte status read captured from DOGTRA_aff8 (0x2A59): 3 barks
        raw_status = bytes.fromhex(
            "010500010164641605000000000000ef02112f00000300000000"
        )
        delta = decoder.decode_characteristic_payload(
            UUID_DOGTRA_STATUS_CHAR, raw_status, state
        )
        self.assertEqual(delta, 0)
        self.assertEqual(state.packet_type, 1)
        self.assertEqual(state.collar_mode, "Count Only")
        self.assertEqual(state.battery_level, 100)
        self.assertEqual(state.bark_count, 3)
        self.assertEqual(state.whine_count, 0)
        self.assertEqual(state.uptime_formatted, "02:17:47")
        self.assertEqual(state.uptime_seconds, 2 * 3600 + 17 * 60 + 47)

        # Real 26-byte status read captured from DOGTRA_aff8 (0x2A59): 4 barks (+1)
        raw_next = bytes.fromhex(
            "010500010164641605010000010000ef02151c00000400000000"
        )
        delta2 = decoder.decode_characteristic_payload(
            UUID_DOGTRA_STATUS_CHAR, raw_next, state
        )
        self.assertEqual(delta2, 1)
        self.assertEqual(state.bark_count, 4)
        self.assertEqual(state.historic_bark_count, 4)
        # Real 26-byte status read captured from DOGTRA_aff8 (0x2A59): 5 barks, 1 whine/howl, sound=3, vib=4
        raw_whine = bytes.fromhex(
            "010500010164640e040000000000006f02202700000500000001"
        )
        delta3 = decoder.decode_characteristic_payload(
            UUID_DOGTRA_STATUS_CHAR, raw_whine, state
        )
        self.assertEqual(delta3, 1)
        self.assertEqual(state.bark_count, 5)
        self.assertEqual(state.whine_count, 1)
        self.assertEqual(state.sound_sensitivity, 3)
        self.assertEqual(state.vibration_sensitivity, 4)
        # Real 26-byte status read captured from DOGTRA_aff8 (0x2A59): Pager mode (byte 1 = 2)
        raw_pager = bytes.fromhex(
            "010200010164640e04000000000000ef02233400000500000001"
        )
        decoder.decode_characteristic_payload(
            UUID_DOGTRA_STATUS_CHAR, raw_pager, state
        )
        self.assertEqual(state.collar_mode, "Pager (Vibration)")
        self.assertEqual(state.collar_mode_id, 2)
        self.assertEqual(state.bark_count, 5)
        self.assertEqual(state.howl_count, 0)
        self.assertEqual(state.whine_count, 1)
        self.assertEqual(state.whine_and_howl_count, 1)
        self.assertEqual(state.battery_level, 100)
        self.assertEqual(state.auto_increase_max_level, 100)
        self.assertEqual(state.alarm, "None")
        self.assertTrue(state.individual_settings["detect_bark"])
        self.assertTrue(state.individual_settings["detect_howl"])
        self.assertTrue(state.individual_settings["detect_whine"])

        # Synthetic 26-byte packet with battery=72% (byte 6) != auto_inc_max=100 (byte 5),
        # howl=2 (bytes 22..23), whine=3 (bytes 24..25), alarm=0x0A (3h wear reminder)
        raw_spec = bytes.fromhex(
            "010300020164480e04000000000000ef0300000a000700020003"
        )
        decoder.decode_characteristic_payload(
            UUID_DOGTRA_STATUS_CHAR, raw_spec, state
        )
        self.assertEqual(state.collar_mode, "Auto Increase")
        self.assertEqual(state.auto_increase_max_level, 100)
        self.assertEqual(state.battery_level, 72)
        self.assertEqual(state.bark_count, 7)
        self.assertEqual(state.howl_count, 2)
        self.assertEqual(state.whine_count, 3)
        self.assertEqual(state.whine_and_howl_count, 5)
        self.assertEqual(state.alarm, "Extended Wear Reminder (3 Hour)")

    def test_build_set_mode_command_preserves_settings(self) -> None:
        current = bytes.fromhex(
            "010500010164640e04000000000000ef02233400000500000001"
        )
        cmd = build_set_mode_command(2, current)  # Switch Count Only (5) -> Pager (2)
        self.assertEqual(len(cmd), 26)
        self.assertEqual(cmd[0], 0x02)  # Instruction.MODE
        self.assertEqual(cmd[1], 0x02)  # Pager
        self.assertEqual(cmd[2:], current[2:])  # All other settings preserved

    def test_build_reset_session_command(self) -> None:
        current = bytes.fromhex(
            "010500010164640e04000000000000ef02233400000500000001"
        )
        cmd = build_reset_session_command(current)
        self.assertEqual(len(cmd), 26)
        self.assertEqual(cmd[0], 0xB0)  # Instruction.RESET_DEVICE_TIME
        self.assertEqual(cmd[1:16], current[1:16])  # Settings preserved
        self.assertEqual(cmd[16:], bytes(10))  # Time & totals zeroed

    def test_build_set_sensitivity_command(self) -> None:
        current = bytes.fromhex(
            "010500010164640e04000000000000ef02233400000500000001"
        )
        # Update volume sensitivity from 3 (0x0E) -> 5 (0x16), keep vibration = 4
        cmd_vol = build_set_sensitivity_command(
            current_state_bytes=current, sound_level=5
        )
        self.assertEqual(len(cmd_vol), 26)
        self.assertEqual(cmd_vol[0], 0xB5)
        self.assertEqual(cmd_vol[7], 0x16)
        self.assertEqual(cmd_vol[8], 0x04)
        self.assertEqual(cmd_vol[1:7], current[1:7])

        # Update vibration sensitivity from 4 -> 2, keep volume = 3 (0x0E)
        cmd_vib = build_set_sensitivity_command(
            current_state_bytes=current, vibration_level=2
        )
        self.assertEqual(cmd_vib[0], 0xB5)
        self.assertEqual(cmd_vib[7], 0x0E)
        self.assertEqual(cmd_vib[8], 0x02)

    def test_build_commands_reject_missing_or_short_state_frame(self) -> None:
        for bad_frame in (b"", b"\x01" * 10, b"\x01" * 25):
            with self.assertRaises(ValueError):
                build_set_mode_command(2, bad_frame)
            with self.assertRaises(ValueError):
                build_reset_session_command(bad_frame)
            with self.assertRaises(ValueError):
                build_set_sensitivity_command(bad_frame, sound_level=3)

    def test_explicit_offsets_still_caches_26b_state_frame(self) -> None:
        config = ParserConfig(bark_byte_offset=20, bark_byte_length=2, little_endian=False)
        state = DogtraCollarState(address="FD:23:3C:AF:AF:F8")
        decoder = DogtraProtocolDecoder(config)

        raw = bytes.fromhex("010500010164640e04000000000000ef02233400000500000001")
        decoder.decode_characteristic_payload(UUID_DOGTRA_STATUS_CHAR, raw, state)
        self.assertEqual(state.bark_count, 5)
        self.assertEqual(state.last_raw_state_bytes, raw)

    def test_historic_bark_count_survives_resets(self) -> None:
        state = DogtraCollarState(address="FD:23:3C:AF:AF:F8")
        decoder = DogtraProtocolDecoder()

        def make_pkt(barks: int) -> bytes:
            buf = bytearray(bytes.fromhex("010500010164640e04000000000000ef02233400000000000000"))
            buf[20:22] = barks.to_bytes(2, "big")
            return bytes(buf)

        # 1. Initial read with 5 barks seeds historic_bark_count = 5
        decoder.decode_characteristic_payload(UUID_DOGTRA_STATUS_CHAR, make_pkt(5), state)
        self.assertEqual(state.historic_bark_count, 5)

        # 2. Barks increase 5 -> 7 (+2)
        decoder.decode_characteristic_payload(UUID_DOGTRA_STATUS_CHAR, make_pkt(7), state)
        self.assertEqual(state.historic_bark_count, 7)

        # 3. Collar resets to 0 -> historic stays 7
        decoder.decode_characteristic_payload(UUID_DOGTRA_STATUS_CHAR, make_pkt(0), state)
        self.assertEqual(state.bark_count, 0)
        self.assertEqual(state.historic_bark_count, 7)

        # 4. New barks after reset 0 -> 3 (+3 -> 10)
        decoder.decode_characteristic_payload(UUID_DOGTRA_STATUS_CHAR, make_pkt(3), state)
        self.assertEqual(state.historic_bark_count, 10)

        # 5. Collar reset while offline and barked 2 times (3 -> 2 -> +2 -> 12)
        decoder.decode_characteristic_payload(UUID_DOGTRA_STATUS_CHAR, make_pkt(2), state)
        self.assertEqual(state.historic_bark_count, 12)

        # 6. Simulate HA restart restoring historic_bark_count = 100
        restored_state = DogtraCollarState(address="FD:23:3C:AF:AF:F8", historic_bark_count=100)
        decoder.decode_characteristic_payload(UUID_DOGTRA_STATUS_CHAR, make_pkt(5), restored_state)
        self.assertEqual(restored_state.historic_bark_count, 100)
        decoder.decode_characteristic_payload(UUID_DOGTRA_STATUS_CHAR, make_pkt(6), restored_state)
        self.assertEqual(restored_state.historic_bark_count, 101)

    def test_standard_battery_characteristic(self) -> None:
        state = DogtraCollarState(address="AA:BB:CC:DD:EE:FF")
        decoder = DogtraProtocolDecoder()

        delta = decoder.decode_characteristic_payload(
            UUID_BATTERY_LEVEL_CHAR, bytes([87]), state
        )
        self.assertEqual(delta, 0)
        self.assertEqual(state.battery_level, 87)

    def test_framed_binary_packet_and_bark_delta(self) -> None:
        state = DogtraCollarState(address="AA:BB:CC:DD:EE:FF")
        decoder = DogtraProtocolDecoder()

        # First packet: SOF=0xAA,0x55, CMD=0x01, MODE=2 (Pager), BATT=90%, BARKS=12 (0x000C LE), WHINES=3 (0x0003 LE)
        pkt1 = bytes([0xAA, 0x55, 0x01, 0x02, 90, 0x0C, 0x00, 0x03, 0x00])
        delta1 = decoder.decode_characteristic_payload(
            "0000fff1-0000-1000-8000-00805f9b34fb", pkt1, state
        )
        self.assertEqual(delta1, 0)  # First reading sets baseline
        self.assertEqual(state.bark_count, 12)
        self.assertEqual(state.whine_count, 3)
        self.assertEqual(state.battery_level, 90)
        self.assertEqual(state.collar_mode, "Pager (Vibration)")
        self.assertEqual(state.historic_bark_count, 12)

        # Second packet: BARKS increased from 12 -> 15 (+3)
        pkt2 = bytes([0xAA, 0x55, 0x01, 0x02, 89, 0x0F, 0x00, 0x04, 0x00])
        delta2 = decoder.decode_characteristic_payload(
            "0000fff1-0000-1000-8000-00805f9b34fb", pkt2, state
        )
        self.assertEqual(delta2, 3)
        self.assertEqual(state.bark_count, 15)
        self.assertEqual(state.whine_count, 4)
        self.assertEqual(state.battery_level, 89)
        self.assertEqual(state.historic_bark_count, 15)
        self.assertIsNotNone(state.last_bark_time)

    def test_explicit_byte_offsets_override(self) -> None:
        config = ParserConfig(
            bark_byte_offset=2,
            bark_byte_length=2,
            whine_byte_offset=4,
            whine_byte_length=2,
            battery_byte_offset=1,
            mode_byte_offset=0,
            little_endian=False,  # Big-endian
        )
        state = DogtraCollarState(address="AA:BB:CC:DD:EE:FF")
        decoder = DogtraProtocolDecoder(config)

        # Mode=5 (Count Only), Battery=85%, Barks=0x0102 (258 BE), Whines=0x0005 (5 BE)
        payload = bytes([0x05, 85, 0x01, 0x02, 0x00, 0x05])
        decoder.decode_characteristic_payload("custom-uuid", payload, state)

        self.assertEqual(state.collar_mode, "Count Only")
        self.assertEqual(state.battery_level, 85)
        self.assertEqual(state.bark_count, 258)
        self.assertEqual(state.whine_count, 5)

    def test_text_and_json_payloads(self) -> None:
        state = DogtraCollarState(address="AA:BB:CC:DD:EE:FF")
        decoder = DogtraProtocolDecoder()

        decoder.decode_characteristic_payload(
            "6e400003-b5a3-f393-e0a9-e50e24dcca9e",
            b'{"bark": 7, "whine": 2, "bat": 75, "mode": 5}',
            state,
        )
        self.assertEqual(state.bark_count, 7)
        self.assertEqual(state.whine_count, 2)
        self.assertEqual(state.battery_level, 75)
        self.assertEqual(state.collar_mode, "Count Only")

        delta = decoder.decode_characteristic_payload(
            "6e400003-b5a3-f393-e0a9-e50e24dcca9e",
            b"BARK:9,WHINE:2,BAT:74,MODE:2",
            state,
        )
        self.assertEqual(delta, 2)
        self.assertEqual(state.bark_count, 9)
        self.assertEqual(state.historic_bark_count, 9)
        self.assertEqual(state.collar_mode, "Pager (Vibration)")


if __name__ == "__main__":
    unittest.main()
