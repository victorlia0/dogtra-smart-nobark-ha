#!/usr/bin/env python3
"""Live BLE Scanner & GATT Profiler for the Dogtra Smart NoBark collar.

Run this script when your Dogtra Smart NoBark collar is powered on and within BLE range:
    python tools/discover_collar_ble.py

It will:
  1. Scan for nearby BLE devices and highlight Dogtra / NoBark candidates.
  2. Connect to the selected collar and enumerate all GATT services, characteristics, and descriptors.
  3. Read all readable characteristics and listen for notifications for 30 seconds.
  4. Save a complete `collar_gatt_profile.json` file that can be used to lock in exact UUIDs.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from bleak import BleakClient, BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic


async def scan_and_profile(
    target_address: str | None,
    listen_seconds: int,
    output_path: Path,
) -> int:
    print("Scanning for BLE devices (8 seconds)...")
    discovered = await BleakScanner.discover(timeout=8.0, return_adv=True)

    candidates: list[tuple[str, str, int, dict[str, Any]]] = []
    for addr, (dev, adv) in discovered.items():
        name = dev.name or adv.local_name or ""
        mfg_hex = {f"0x{k:04x}": v.hex() for k, v in adv.manufacturer_data.items()}
        candidates.append(
            (
                addr,
                name,
                adv.rssi,
                {
                    "service_uuids": adv.service_uuids,
                    "manufacturer_data": mfg_hex,
                },
            )
        )

    candidates.sort(
        key=lambda item: (
            not any(
                tag in item[1].upper()
                for tag in ("DOGTRA", "NOBARK", "NO BARK", "SNB")
            ),
            -item[2],
        )
    )

    print(f"\nFound {len(candidates)} BLE device(s):")
    for idx, (addr, name, rssi, meta) in enumerate(candidates, 1):
        label = name or "<Unknown>"
        print(
            f"  [{idx:2d}] {addr} | RSSI: {rssi:4d} dBm | Name: {label:22s} | UUIDs: {meta['service_uuids']} | Mfg: {meta['manufacturer_data']}"
        )

    if not target_address:
        for addr, name, _, _ in candidates:
            if any(
                tag in name.upper()
                for tag in ("DOGTRA", "NOBARK", "NO BARK", "SNB")
            ):
                target_address = addr
                print(f"\nAuto-selected Dogtra candidate: {name} ({addr})")
                break

    if not target_address:
        print(
            "\nNo Dogtra collar auto-detected. Re-run with `--address AA:BB:CC:DD:EE:FF` once your collar is turned on."
        )
        return 1

    print(f"\nConnecting to {target_address}...")
    profile_report: dict[str, Any] = {
        "address": target_address,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "services": {},
        "initial_reads": {},
        "notifications": [],
    }

    async with BleakClient(target_address, timeout=20.0) as client:
        print(f"Connected: {client.is_connected}")

        def _on_notify(char: BleakGATTCharacteristic, data: bytearray) -> None:
            payload = bytes(data)
            entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "characteristic": char.uuid,
                "handle": char.handle,
                "hex": payload.hex(),
                "bytes": list(payload),
            }
            print(f"  [NOTIFY] {char.uuid}: {payload.hex()} ({list(payload)})")
            profile_report["notifications"].append(entry)

        for service in client.services:
            print(f"\n[Service] {service.uuid} ({service.description})")
            chars_list = []
            for char in service.characteristics:
                props = list(char.properties)
                print(
                    f"  [Char] {char.uuid} (handle={char.handle}, props={props}) - {char.description}"
                )
                chars_list.append(
                    {
                        "uuid": char.uuid,
                        "handle": char.handle,
                        "description": char.description,
                        "properties": props,
                    }
                )
                if "read" in props:
                    try:
                        val = bytes(await client.read_gatt_char(char))
                        print(f"         Read value: {val.hex()} | {val!r}")
                        profile_report["initial_reads"][char.uuid] = {
                            "hex": val.hex(),
                            "bytes": list(val),
                        }
                    except Exception as exc:  # noqa: BLE001
                        print(f"         Read error: {exc}")

                if "notify" in props or "indicate" in props:
                    try:
                        await client.start_notify(char, _on_notify)
                        print(f"         Subscribed to notifications on {char.uuid}")
                    except Exception as exc:  # noqa: BLE001
                        print(f"         Notify subscribe error: {exc}")

            profile_report["services"][service.uuid] = {
                "description": service.description,
                "characteristics": chars_list,
            }

        print(
            f"\nListening for BLE notifications for {listen_seconds} seconds (try triggering a bark count or mode change on the collar)..."
        )
        await asyncio.sleep(listen_seconds)

    output_path.write_text(json.dumps(profile_report, indent=2), encoding="utf-8")
    print(f"\nSaved complete GATT profile and packet capture to {output_path.resolve()}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Discover and profile a Dogtra Smart NoBark collar over BLE"
    )
    parser.add_argument(
        "--address",
        help="Bluetooth MAC address of the collar (auto-detected by name if omitted)",
    )
    parser.add_argument(
        "--listen",
        type=int,
        default=30,
        help="Seconds to listen for BLE notifications after connecting (default: 30)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("collar_gatt_profile.json"),
        help="Output JSON file path",
    )
    args = parser.parse_args()
    return asyncio.run(scan_and_profile(args.address, args.listen, args.output))


if __name__ == "__main__":
    raise SystemExit(main())
