#!/usr/bin/env python3
"""Extract BLE GATT UUIDs and protocol strings from the DOGTRA SMART NOBARK APK.

Usage:
  1. If you have the APK / XAPK / APKS file downloaded or exported from your phone:
       python tools/extract_apk_protocol.py path/to/com.dogtra.smartnobark.apk

  2. If your Android phone with DOGTRA SMART NOBARK installed is connected via USB (ADB):
       python tools/extract_apk_protocol.py --adb
"""

from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import zipfile

UUID_REGEX = re.compile(
    rb"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

BLE_CONTEXT_REGEX = re.compile(
    rb"[ -~]{4,120}(?:uuid|gatt|ble|bark|whine|howl|dogtra|nobark|battery|notify|characteristic|service)[ -~]{0,120}",
    re.IGNORECASE,
)

STANDARD_BLE_BASE = "-0000-1000-8000-00805f9b34fb"


def pull_apk_via_adb(package: str, out_dir: Path) -> list[Path]:
    """Pull APK split files for the given package from a connected Android device via ADB."""
    print(f"Querying ADB for installed package: {package}...")
    res = subprocess.run(
        ["adb", "shell", "pm", "path", package],
        capture_output=True,
        text=True,
        check=False,
    )
    if res.returncode != 0 or not res.stdout.strip():
        raise RuntimeError(
            f"Could not find {package} via ADB. Ensure USB debugging is enabled and `adb devices` lists your phone."
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    pulled: list[Path] = []
    for line in res.stdout.splitlines():
        line = line.strip()
        if line.startswith("package:"):
            remote_path = line.split("package:", 1)[1].strip()
            local_path = out_dir / Path(remote_path).name
            print(f"Pulling {remote_path} -> {local_path}...")
            subprocess.run(["adb", "pull", remote_path, str(local_path)], check=True)
            pulled.append(local_path)
    return pulled


def scan_binary_blob(
    source_name: str,
    data: bytes,
    uuids_found: dict[str, set[str]],
    ble_strings: set[str],
) -> None:
    """Scan raw binary bytes (DEX, .so, JS bundle, Dart snapshot) for UUIDs and BLE strings."""
    for match in UUID_REGEX.finditer(data):
        uuid_str = match.group(0).decode("ascii").lower()
        uuids_found.setdefault(uuid_str, set()).add(source_name)

    # Also check UTF-16LE encoded UUID strings (used in some Kotlin/Java/Xamarin resources)
    for match in re.finditer(
        rb"(?:[0-9a-fA-F]\x00){8}-\x00(?:[0-9a-fA-F]\x00){4}-\x00(?:[0-9a-fA-F]\x00){4}-\x00(?:[0-9a-fA-F]\x00){4}-\x00(?:[0-9a-fA-F]\x00){12}",
        data,
    ):
        uuid_str = match.group(0).decode("utf-16le").lower()
        uuids_found.setdefault(uuid_str, set()).add(f"{source_name} (utf16)")

    for match in BLE_CONTEXT_REGEX.finditer(data):
        text = match.group(0).decode("ascii", errors="ignore").strip()
        if len(text) >= 6 and not text.startswith("http"):
            ble_strings.add(f"[{source_name}] {text}")


def analyze_archive(
    archive_path: Path,
    uuids_found: dict[str, set[str]],
    ble_strings: set[str],
) -> None:
    """Inspect an APK, XAPK, or APKS zip archive recursively."""
    with zipfile.ZipFile(archive_path, "r") as zf:
        for info in zf.infolist():
            name_lower = info.filename.lower()
            if name_lower.endswith(".apk"):
                # Nested APK inside XAPK / APKS bundle
                nested_bytes = zf.read(info.filename)
                with zipfile.ZipFile(io.BytesIO(nested_bytes), "r") as nested_zf:
                    for ninfo in nested_zf.infolist():
                        nname = ninfo.filename.lower()
                        if _is_interesting_entry(nname):
                            scan_binary_blob(
                                f"{info.filename}!{ninfo.filename}",
                                nested_zf.read(ninfo.filename),
                                uuids_found,
                                ble_strings,
                            )
            elif _is_interesting_entry(name_lower):
                scan_binary_blob(
                    info.filename,
                    zf.read(info.filename),
                    uuids_found,
                    ble_strings,
                )


def _is_interesting_entry(entry_name: str) -> bool:
    """Return True if an archive entry can contain code or BLE constants."""
    return (
        entry_name.endswith(".dex")
        or entry_name.endswith(".so")
        or entry_name.endswith(".bundle")
        or entry_name.endswith(".arsc")
        or entry_name.endswith(".json")
        or "flutter_assets" in entry_name
        or "assets/" in entry_name
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract BLE GATT UUIDs and protocol strings from DOGTRA SMART NOBARK APK"
    )
    parser.add_argument(
        "apk_paths",
        nargs="*",
        type=Path,
        help="Path(s) to .apk, .xapk, or .apks files",
    )
    parser.add_argument(
        "--adb",
        action="store_true",
        help="Pull com.dogtra.smartnobark directly from an ADB-connected Android phone",
    )
    parser.add_argument(
        "--package",
        default="com.dogtra.smartnobark",
        help="Android package name when using --adb (default: com.dogtra.smartnobark)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("extracted_ble_protocol.json"),
        help="Output JSON report path",
    )
    args = parser.parse_args()

    target_files: list[Path] = list(args.apk_paths)
    if args.adb:
        target_files.extend(pull_apk_via_adb(args.package, Path("pulled_apk")))

    if not target_files:
        parser.print_help()
        return 1

    uuids_found: dict[str, set[str]] = {}
    ble_strings: set[str] = set()

    for path in target_files:
        if not path.exists():
            print(f"File not found: {path}", file=sys.stderr)
            continue
        print(f"Scanning {path}...")
        analyze_archive(path, uuids_found, ble_strings)

    custom_uuids = {
        u: sorted(sources)
        for u, sources in sorted(uuids_found.items())
        if not u.endswith(STANDARD_BLE_BASE)
        or u.startswith(("0000ff", "0000fe", "0000ae"))
    }
    standard_uuids = {
        u: sorted(sources)
        for u, sources in sorted(uuids_found.items())
        if u not in custom_uuids
    }

    report = {
        "custom_ble_uuids": custom_uuids,
        "standard_ble_uuids": standard_uuids,
        "ble_protocol_strings": sorted(ble_strings)[:300],
    }

    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\n=== Extraction Complete ===")
    print(f"Custom / Vendor UUIDs found: {len(custom_uuids)}")
    for u, srcs in custom_uuids.items():
        print(f"  - {u}  (in {', '.join(srcs[:3])})")
    print(f"Standard SIG UUIDs found:    {len(standard_uuids)}")
    for u, srcs in standard_uuids.items():
        print(f"  - {u}  (in {', '.join(srcs[:3])})")
    print(f"Protocol strings captured:   {len(ble_strings)}")
    print(f"Full report saved to:        {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
