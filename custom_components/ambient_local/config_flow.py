"""Config and options (incl. AP-mode recovery) flow for Ambient Weather Local."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .console import ConsoleClient, ConsoleError, detect_local_ip
from .const import (
    AP_SSID_PREFIX,
    CONF_CONSOLE_IP,
    CONF_DEVICE_NAME,
    CONF_LISTEN_PORT,
    CONF_SCAN_MINUTES,
    DEFAULT_DEVICE_NAME,
    DEFAULT_LISTEN_PORT,
    DEFAULT_SCAN_MINUTES,
    DOMAIN,
    STORE_KEY,
    STORE_VERSION,
)
from .provision import ProvisionError, find_setup_ap, manual_instructions, provision_via_ap
from .supervisor import SupervisorNetwork, supervisor_available

_LOGGER = logging.getLogger(__name__)


async def _load_cache(hass) -> dict:
    """Load the persisted console-config snapshot (survives entry deletion)."""
    return await Store(hass, STORE_VERSION, STORE_KEY).async_load() or {}


def _pick_schema(candidates: list[str], default_ssid: str) -> vol.Schema:
    ssid_field: Any = vol.In(candidates) if candidates else str
    default = default_ssid if (not candidates or default_ssid in candidates) else candidates[0]
    return vol.Schema(
        {
            vol.Required("target_ssid", default=default): ssid_field,
            vol.Required("target_psk"): str,
        }
    )


def _candidates(aps: list[dict]) -> list[str]:
    """2.4 GHz SSIDs (excluding the console's own setup AP) to offer as targets."""
    return sorted(
        {
            a["ssid"]
            for a in aps
            if a.get("ssid")
            and not a["ssid"].upper().startswith(AP_SSID_PREFIX.upper())
            and a.get("frequency", 0) < 3000
        }
    )


class AmbientConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup (incl. provisioning a reset console in AP mode)."""

    VERSION = 1

    def __init__(self) -> None:
        self._pending: dict[str, Any] = {}
        self._iface: str | None = None
        self._ap_ssid: str | None = None
        self._candidates: list[str] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        # No IP is asked for — the console's address is learned from its data
        # push. This step just collects a name and the listener port, then
        # detects the console (AP setup if it's in AP mode, otherwise it's
        # discovered from its data).
        if user_input is not None:
            self._pending = user_input
            return await self.async_step_detect()

        schema = vol.Schema(
            {
                vol.Required(CONF_DEVICE_NAME, default=DEFAULT_DEVICE_NAME): str,
                vol.Required(CONF_LISTEN_PORT, default=DEFAULT_LISTEN_PORT): int,
                vol.Required(CONF_SCAN_MINUTES, default=DEFAULT_SCAN_MINUTES): int,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    async def _create(self) -> ConfigFlowResult:
        cached = await _load_cache(self.hass)
        mac = (cached.get("network") or {}).get("mac")
        if mac:
            await self.async_set_unique_id(mac.lower())
            self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=self._pending.get(CONF_DEVICE_NAME, DEFAULT_DEVICE_NAME),
            data={
                CONF_LISTEN_PORT: self._pending[CONF_LISTEN_PORT],
                CONF_DEVICE_NAME: self._pending.get(CONF_DEVICE_NAME, DEFAULT_DEVICE_NAME),
                CONF_SCAN_MINUTES: self._pending[CONF_SCAN_MINUTES],
            },
        )

    async def _ha_ip(self, sup: SupervisorNetwork | None) -> str:
        """Home Assistant's own LAN IP — for the console's Custom Server target."""
        if sup is not None:
            try:
                info = await sup.info()
                for iface in info.get("interfaces", []):
                    if iface.get("primary"):
                        addrs = (iface.get("ipv4") or {}).get("address") or []
                        if addrs:
                            return addrs[0].split("/")[0]
            except Exception:  # noqa: BLE001
                pass
        return "<home-assistant-ip>"

    async def async_step_detect(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Provision over the console's AP if it's broadcasting; else await push."""
        session = async_get_clientsession(self.hass)
        cached = await _load_cache(self.hass)
        mac = (cached.get("network") or {}).get("mac")

        sup = SupervisorNetwork(session) if supervisor_available() else None
        self._iface = await sup.spare_wifi_interface() if sup else None
        if self._iface:
            try:
                aps = await sup.scan(self._iface)
            except Exception:  # noqa: BLE001
                aps = []
            self._ap_ssid = find_setup_ap(aps, mac)
            if self._ap_ssid:
                self._candidates = _candidates(aps)
                return await self.async_step_pick_wifi()

        # No setup AP found (or no spare radio). The console is — or will be —
        # on the network; HA discovers its IP from the first data push. If it
        # needs a spare radio to recover but there is none, show manual steps.
        if sup is not None and self._iface is None:
            return await self.async_step_manual()
        return await self._create()

    async def async_step_pick_wifi(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        session = async_get_clientsession(self.hass)
        cached = await _load_cache(self.hass)
        default_ssid = (cached.get("network") or {}).get("ssid") or ""

        if user_input is not None:
            sup = SupervisorNetwork(session)
            try:
                await provision_via_ap(
                    session, sup, self._iface,
                    user_input["target_ssid"], user_input["target_psk"],
                    cached, await self._ha_ip(sup), self._pending[CONF_LISTEN_PORT],
                )
            except ProvisionError as err:
                return self.async_show_form(
                    step_id="pick_wifi",
                    data_schema=_pick_schema(self._candidates, default_ssid),
                    errors={"base": "provision_failed"},
                    description_placeholders={"error": str(err), "ap": self._ap_ssid},
                )
            # The console reboots onto Wi-Fi and starts pushing to us; HA learns
            # its IP from that push. No address is entered anywhere.
            return await self._create()

        return self.async_show_form(
            step_id="pick_wifi",
            data_schema=_pick_schema(self._candidates, default_ssid),
            description_placeholders={"ap": self._ap_ssid},
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return await self._create()
        session = async_get_clientsession(self.hass)
        cached = await _load_cache(self.hass)
        mac = (cached.get("network") or {}).get("mac")
        sup = SupervisorNetwork(session) if supervisor_available() else None
        text = manual_instructions(
            cached, await self._ha_ip(sup), self._pending[CONF_LISTEN_PORT], mac
        )
        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema({}),
            description_placeholders={"instructions": text},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return AmbientOptionsFlow()


class AmbientOptionsFlow(OptionsFlow):
    """Settings plus the AP-mode 'recover console' wizard."""

    def __init__(self) -> None:
        self._iface: str | None = None
        self._ap_ssid: str | None = None
        self._candidates: list[str] = []

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(step_id="init", menu_options=["settings", "provision"])

    # --- plain settings -----------------------------------------------------

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        data = {**self.config_entry.data, **self.config_entry.options}
        schema = vol.Schema(
            {
                vol.Required(CONF_DEVICE_NAME, default=data.get(CONF_DEVICE_NAME, DEFAULT_DEVICE_NAME)): str,
                vol.Required(CONF_LISTEN_PORT, default=data.get(CONF_LISTEN_PORT, DEFAULT_LISTEN_PORT)): int,
                vol.Required(CONF_SCAN_MINUTES, default=data.get(CONF_SCAN_MINUTES, DEFAULT_SCAN_MINUTES)): int,
            }
        )
        return self.async_show_form(step_id="settings", data_schema=schema)

    # --- recovery / provisioning -------------------------------------------

    def _coordinator(self):
        return self.hass.data[DOMAIN][self.config_entry.entry_id]["coordinator"]

    def _ha_ip(self, coord) -> str:
        return coord.ha_ip or detect_local_ip(coord.client.ip) or "<home-assistant-ip>"

    async def async_step_provision(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        coord = self._coordinator()
        session = async_get_clientsession(self.hass)

        # No spare radio (or not on Supervisor) -> manual instructions.
        sup = SupervisorNetwork(session) if supervisor_available() else None
        self._iface = await sup.spare_wifi_interface() if sup else None
        if not self._iface:
            return await self.async_step_manual()

        try:
            aps = await sup.scan(self._iface)
        except Exception:  # noqa: BLE001
            aps = []
        self._ap_ssid = find_setup_ap(aps, coord.station_mac)
        if not self._ap_ssid:
            # console isn't broadcasting its setup AP yet
            if user_input is not None:  # user pressed "retry"
                pass
            expected = AP_SSID_PREFIX + (
                (coord.station_mac or "").replace(":", "")[-6:].upper() or "XXXXXX"
            )
            return self.async_show_form(
                step_id="provision",
                data_schema=vol.Schema({vol.Required("retry", default=True): bool}),
                errors={"base": "ap_not_found"},
                description_placeholders={"ap": expected},
            )

        # 2.4 GHz networks the radio can see, as target options
        self._candidates = sorted(
            {
                a["ssid"]
                for a in aps
                if a.get("ssid")
                and not a["ssid"].upper().startswith(AP_SSID_PREFIX.upper())
                and a.get("frequency", 0) < 3000
            }
        )
        return await self.async_step_pick()

    async def async_step_pick(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        coord = self._coordinator()
        session = async_get_clientsession(self.hass)
        cached_ssid = (coord.cached.get("network") or {}).get("ssid") or ""

        if user_input is not None:
            sup = SupervisorNetwork(session)
            try:
                await provision_via_ap(
                    session,
                    sup,
                    self._iface,
                    user_input["target_ssid"],
                    user_input["target_psk"],
                    coord.cached,
                    self._ha_ip(coord),
                    coord.listen_port,
                )
            except ProvisionError as err:
                return self.async_show_form(
                    step_id="pick",
                    data_schema=self._pick_schema(cached_ssid),
                    errors={"base": "provision_failed"},
                    description_placeholders={"error": str(err), "ap": self._ap_ssid},
                )
            return self.async_abort(reason="provision_done")

        return self.async_show_form(
            step_id="pick",
            data_schema=self._pick_schema(cached_ssid),
            description_placeholders={"ap": self._ap_ssid},
        )

    def _pick_schema(self, default_ssid: str) -> vol.Schema:
        ssid_field: Any = vol.In(self._candidates) if self._candidates else str
        default = default_ssid if (not self._candidates or default_ssid in self._candidates) else self._candidates[0]
        return vol.Schema(
            {
                vol.Required("target_ssid", default=default): ssid_field,
                vol.Required("target_psk"): str,
            }
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        coord = self._coordinator()
        text = manual_instructions(
            coord.cached, self._ha_ip(coord), coord.listen_port, coord.station_mac
        )
        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema({}),
            description_placeholders={"instructions": text},
        )
