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
    AP_HOST,
    AP_SSID_PREFIX,
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
from .provision import (
    ProvisionError,
    build_network_payload,
    build_ws_payload,
    find_setup_ap,
    manual_instructions,
    provision_via_ap,
)
from .supervisor import SupervisorNetwork, supervisor_available

_LOGGER = logging.getLogger(__name__)


async def _load_cache(hass) -> dict:
    """Load the persisted console-config snapshot (survives entry deletion)."""
    return await Store(hass, STORE_VERSION, STORE_KEY).async_load() or {}


async def _save_console_ip(hass, ip: str) -> None:
    """Record the console's IP so the coordinator can self-heal immediately."""
    store = Store(hass, STORE_VERSION, STORE_KEY)
    data = await store.async_load() or {}
    data["ip"] = ip
    await store.async_save(data)


def _pick_schema(candidates: list[str], default_ssid: str) -> vol.Schema:
    ssid_field: Any = vol.In(candidates) if candidates else str
    default = (
        default_ssid
        if (not candidates or default_ssid in candidates)
        else candidates[0]
    )
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
        self._console_ssids: list[str] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        # 1) Is the console already on the network? Then we have all we need.
        cached = await _load_cache(self.hass)
        known_ip = cached.get("ip")
        if known_ip:
            client = ConsoleClient(async_get_clientsession(self.hass), known_ip)
            try:
                settings = await client.get_settings(timeout_s=4)
            except ConsoleError:
                settings = None
            if settings:
                return await self._create_on_network(known_ip, settings)

        # 2) Not on the network — find a spare radio and the console's setup AP.
        return await self.async_step_provision()

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

    async def _create_on_network(self, ip: str, settings: dict) -> ConfigFlowResult:
        """Console reachable on the LAN — add it directly with defaults."""
        mac = settings.get("sta_mac")
        if mac:
            await self.async_set_unique_id(mac.lower())
            self._abort_if_unique_id_configured()
        await _save_console_ip(self.hass, ip)  # coordinator self-heals immediately
        return self.async_create_entry(
            title=DEFAULT_DEVICE_NAME,
            data={
                CONF_DEVICE_NAME: DEFAULT_DEVICE_NAME,
                CONF_LISTEN_PORT: DEFAULT_LISTEN_PORT,
                CONF_SCAN_MINUTES: DEFAULT_SCAN_MINUTES,
            },
        )

    def _retry_form(self, step: str, error: str) -> ConfigFlowResult:
        return self.async_show_form(
            step_id=step,
            data_schema=vol.Schema({vol.Required("retry", default=True): bool}),
            errors={"base": error},
            description_placeholders={"ap": self._ap_ssid or "AMBWeatherPro-…"},
        )

    async def async_step_provision(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Auto-find the console's setup AP, connect to it, then ask for details."""
        session = async_get_clientsession(self.hass)
        sup = SupervisorNetwork(session) if supervisor_available() else None
        self._iface = await sup.spare_wifi_interface() if sup else None
        if not self._iface:
            # No spare Wi-Fi radio — can't auto-provision; show manual steps.
            return await self.async_step_manual()

        cached = await _load_cache(self.hass)
        mac = (cached.get("network") or {}).get("mac")
        try:
            aps = await sup.scan(self._iface)
        except Exception:  # noqa: BLE001
            aps = []
        self._ap_ssid = find_setup_ap(aps, mac)
        if not self._ap_ssid:
            self._ap_ssid = AP_SSID_PREFIX + (
                (mac or "").replace(":", "")[-6:].upper() or "…"
            )
            return self._retry_form("provision", "ap_not_found")

        # Found it — connect to the AP and confirm we can reach the console.
        try:
            await sup.join(self._iface, self._ap_ssid, psk=None)
        except Exception:  # noqa: BLE001
            return self._retry_form("provision", "ap_join_failed")

        ap_console = ConsoleClient(session, AP_HOST)
        for _ in range(15):
            try:
                await ap_console.get_device_info()
                break
            except ConsoleError:
                await asyncio.sleep(2)
        else:
            await sup.disable(self._iface)
            return self._retry_form("provision", "ap_join_failed")

        # Ask the console which networks *it* can see, for the picker.
        try:
            self._console_ssids = sorted(
                {s.get("ssid") for s in await ap_console.scan_ssids() if s.get("ssid")}
            )
        except ConsoleError:
            self._console_ssids = []
        return await self.async_step_setup()

    def _setup_schema(self, default_ssid: str, default_email: str) -> vol.Schema:
        ssids = self._console_ssids
        ssid_field: Any = vol.In(ssids) if ssids else str
        default = default_ssid if (not ssids or default_ssid in ssids) else ssids[0]
        return vol.Schema(
            {
                vol.Required("target_ssid", default=default): ssid_field,
                vol.Required("target_psk"): str,
                vol.Required(CONF_DEVICE_NAME, default=DEFAULT_DEVICE_NAME): str,
                vol.Required(CONF_LISTEN_PORT, default=DEFAULT_LISTEN_PORT): int,
                vol.Optional("amb_email", default=default_email): str,
            }
        )

    async def async_step_setup(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """One form: Wi-Fi + password + name + port + AmbientWeather.net email."""
        session = async_get_clientsession(self.hass)
        cached = await _load_cache(self.hass)
        default_ssid = (cached.get("network") or {}).get("ssid") or ""
        default_email = (cached.get("ws") or {}).get("ambEmail") or ""

        if user_input is not None:
            sup = SupervisorNetwork(session)
            ap_console = ConsoleClient(session, AP_HOST)
            try:
                ha_ip = await self._ha_ip(sup)
                await ap_console.set_network_info(
                    build_network_payload(
                        cached.get("network") or {},
                        user_input["target_ssid"],
                        user_input["target_psk"],
                    )
                )
                ws = {
                    **(cached.get("ws") or {}),
                    "ambEmail": user_input.get("amb_email", ""),
                }
                await ap_console.set_settings(
                    build_ws_payload(ws, ha_ip, user_input[CONF_LISTEN_PORT])
                )
            except ConsoleError as err:
                await sup.disable(self._iface)
                return self.async_show_form(
                    step_id="setup",
                    data_schema=self._setup_schema(default_ssid, default_email),
                    errors={"base": "provision_failed"},
                    description_placeholders={"error": str(err), "ap": self._ap_ssid},
                )
            await sup.disable(
                self._iface
            )  # release the radio; console reboots to Wi-Fi

            mac = (cached.get("network") or {}).get("mac")
            if mac:
                await self.async_set_unique_id(mac.lower())
                self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=user_input[CONF_DEVICE_NAME],
                data={
                    CONF_DEVICE_NAME: user_input[CONF_DEVICE_NAME],
                    CONF_LISTEN_PORT: user_input[CONF_LISTEN_PORT],
                    CONF_SCAN_MINUTES: DEFAULT_SCAN_MINUTES,
                },
            )

        return self.async_show_form(
            step_id="setup",
            data_schema=self._setup_schema(default_ssid, default_email),
            description_placeholders={"ap": self._ap_ssid},
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                title=DEFAULT_DEVICE_NAME,
                data={
                    CONF_DEVICE_NAME: DEFAULT_DEVICE_NAME,
                    CONF_LISTEN_PORT: DEFAULT_LISTEN_PORT,
                    CONF_SCAN_MINUTES: DEFAULT_SCAN_MINUTES,
                },
            )
        session = async_get_clientsession(self.hass)
        cached = await _load_cache(self.hass)
        mac = (cached.get("network") or {}).get("mac")
        sup = SupervisorNetwork(session) if supervisor_available() else None
        text = manual_instructions(
            cached, await self._ha_ip(sup), DEFAULT_LISTEN_PORT, mac
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
        return self.async_show_menu(
            step_id="init", menu_options=["settings", "provision"]
        )

    # --- plain settings -----------------------------------------------------

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        data = {**self.config_entry.data, **self.config_entry.options}
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_DEVICE_NAME,
                    default=data.get(CONF_DEVICE_NAME, DEFAULT_DEVICE_NAME),
                ): str,
                vol.Required(
                    CONF_LISTEN_PORT,
                    default=data.get(CONF_LISTEN_PORT, DEFAULT_LISTEN_PORT),
                ): int,
                vol.Required(
                    CONF_SCAN_MINUTES,
                    default=data.get(CONF_SCAN_MINUTES, DEFAULT_SCAN_MINUTES),
                ): int,
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
        default = (
            default_ssid
            if (not self._candidates or default_ssid in self._candidates)
            else self._candidates[0]
        )
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
