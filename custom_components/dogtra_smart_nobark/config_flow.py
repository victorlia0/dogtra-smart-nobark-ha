"""Config flow for the Dogtra Smart NoBark integration."""

from __future__ import annotations

import re
from typing import Any

import voluptuous as vol

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_ADDRESS, CONF_NAME
from homeassistant.core import callback

from .const import (
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
    DEFAULT_NAME,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_WHINE_BYTE_LENGTH,
    DEFAULT_WHINE_BYTE_OFFSET,
    DOMAIN,
)

MAC_REGEX = re.compile(r"^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$")


def _is_likely_dogtra(service_info: BluetoothServiceInfoBleak) -> bool:
    """Return True if a discovered BLE device looks like a Dogtra collar."""
    name = (service_info.name or "").upper()
    return any(tag in name for tag in ("DOGTRA", "NOBARK", "NO BARK", "SNB"))


class DogtraSmartNoBarkConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Dogtra Smart NoBark."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovery_info: BluetoothServiceInfoBleak | None = None
        self._discovered_devices: dict[str, BluetoothServiceInfoBleak] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow handler."""
        return DogtraOptionsFlowHandler(config_entry)

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle the bluetooth discovery step."""
        await self.async_set_unique_id(discovery_info.address.upper())
        self._abort_if_unique_id_configured()

        self._discovery_info = discovery_info
        name = discovery_info.name or DEFAULT_NAME
        self.context["title_placeholders"] = {
            "name": name,
            "address": discovery_info.address.upper(),
        }
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm discovery of a Dogtra Smart NoBark collar."""
        assert self._discovery_info is not None
        address = self._discovery_info.address.upper()
        default_name = self._discovery_info.name or DEFAULT_NAME

        if user_input is not None:
            name = user_input.get(CONF_NAME, default_name)
            return self.async_create_entry(
                title=name,
                data={
                    CONF_ADDRESS: address,
                    CONF_NAME: name,
                },
            )

        return self.async_show_form(
            step_id="bluetooth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_NAME, default=default_name): str,
                }
            ),
            description_placeholders={
                "name": default_name,
                "address": address,
            },
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the user step to pick a discovered device or enter MAC manually."""
        errors: dict[str, str] = {}

        if user_input is not None:
            raw_address = user_input[CONF_ADDRESS].strip().upper().replace("-", ":")
            name = user_input.get(CONF_NAME, DEFAULT_NAME).strip() or DEFAULT_NAME

            if not MAC_REGEX.match(raw_address):
                errors[CONF_ADDRESS] = "invalid_mac"
            else:
                await self.async_set_unique_id(raw_address, raise_on_progress=False)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=name,
                    data={
                        CONF_ADDRESS: raw_address,
                        CONF_NAME: name,
                    },
                )

        # Populate discovered connectable BLE devices (prioritizing Dogtra matches)
        current_addresses = self._async_current_ids()
        for info in async_discovered_service_info(self.hass, connectable=True):
            addr = info.address.upper()
            if addr not in current_addresses:
                self._discovered_devices[addr] = info

        # Sort Dogtra-named devices first
        sorted_items = sorted(
            self._discovered_devices.items(),
            key=lambda kv: (not _is_likely_dogtra(kv[1]), kv[1].name or kv[0]),
        )

        # Build suggestions or allow free-text entry (so user can add even when collar is off)
        default_addr = sorted_items[0][0] if sorted_items else ""
        schema = vol.Schema(
            {
                vol.Required(CONF_ADDRESS, default=default_addr): str,
                vol.Optional(CONF_NAME, default=DEFAULT_NAME): str,
            }
        )

        discovered_summary = (
            ", ".join(
                f"{info.name or 'Unknown'} ({addr})"
                for addr, info in sorted_items[:8]
            )
            if sorted_items
            else "None currently broadcasting"
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
            description_placeholders={"discovered_devices": discovered_summary},
        )


class DogtraOptionsFlowHandler(OptionsFlow):
    """Handle options flow for Dogtra Smart NoBark protocol tuning."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the Dogtra Smart NoBark options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        opts = self._config_entry.options
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=opts.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): vol.All(vol.Coerce(int), vol.Range(min=10, max=3600)),
                vol.Optional(
                    CONF_NOTIFY_CHAR_UUID,
                    default=opts.get(CONF_NOTIFY_CHAR_UUID, ""),
                ): str,
                vol.Optional(
                    CONF_READ_CHAR_UUID,
                    default=opts.get(CONF_READ_CHAR_UUID, ""),
                ): str,
                vol.Optional(
                    CONF_WRITE_CHAR_UUID,
                    default=opts.get(CONF_WRITE_CHAR_UUID, ""),
                ): str,
                vol.Optional(
                    CONF_POLL_COMMAND_HEX,
                    default=opts.get(CONF_POLL_COMMAND_HEX, ""),
                ): str,
                vol.Optional(
                    CONF_BARK_BYTE_OFFSET,
                    default=opts.get(CONF_BARK_BYTE_OFFSET, DEFAULT_BARK_BYTE_OFFSET),
                ): vol.Coerce(int),
                vol.Optional(
                    CONF_BARK_BYTE_LENGTH,
                    default=opts.get(CONF_BARK_BYTE_LENGTH, DEFAULT_BARK_BYTE_LENGTH),
                ): vol.In([1, 2, 4]),
                vol.Optional(
                    CONF_WHINE_BYTE_OFFSET,
                    default=opts.get(CONF_WHINE_BYTE_OFFSET, DEFAULT_WHINE_BYTE_OFFSET),
                ): vol.Coerce(int),
                vol.Optional(
                    CONF_WHINE_BYTE_LENGTH,
                    default=opts.get(CONF_WHINE_BYTE_LENGTH, DEFAULT_WHINE_BYTE_LENGTH),
                ): vol.In([1, 2, 4]),
                vol.Optional(
                    CONF_BATTERY_BYTE_OFFSET,
                    default=opts.get(
                        CONF_BATTERY_BYTE_OFFSET, DEFAULT_BATTERY_BYTE_OFFSET
                    ),
                ): vol.Coerce(int),
                vol.Optional(
                    CONF_MODE_BYTE_OFFSET,
                    default=opts.get(CONF_MODE_BYTE_OFFSET, DEFAULT_MODE_BYTE_OFFSET),
                ): vol.Coerce(int),
                vol.Optional(
                    CONF_LITTLE_ENDIAN,
                    default=opts.get(CONF_LITTLE_ENDIAN, DEFAULT_LITTLE_ENDIAN),
                ): bool,
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema)
