"""Config and options (incl. AP-mode recovery) flow for Ambient Weather Local.

Provisioning is *verified* before it is committed: after we hand the console its
Wi-Fi credentials and release the borrowed radio, we wait for the console to
reboot, join that Wi-Fi, and actually reach Home Assistant. Only then do we
create/finish the entry. If the console never reaches us we say exactly why and
what to do, and we never leave a half-configured entry or a stranded radio.
"""

from __future__ import annotations

import asyncio
import contextlib
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
from .listener import PushListener
from .provision import (
    JOIN_FAILED,
    OK,
    PORT_BUSY,
    PROVISION_FAILED,
    UNREACHABLE,
    ProvisionResult,
    find_setup_ap,
    join_and_reach,
    manual_instructions,
    provision_and_verify,
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


async def _release(sup: SupervisorNetwork, interface: str | None) -> None:
    """Release the borrowed radio, never raising — never strand it enabled."""
    if interface:
        with contextlib.suppress(Exception):
            await sup.disable(interface)


def _reason_text(status: str, ssid: str, port: int, detail: str) -> str:
    """A specific, actionable explanation for a failed provisioning attempt."""
    if status == JOIN_FAILED:
        return (
            f"The console tried to join “{ssid}” but couldn't. That is almost always "
            "a wrong Wi-Fi password, or the network isn't 2.4 GHz / is out of range."
        )
    if status == PROVISION_FAILED:
        return (
            "Home Assistant connected to the console's setup network, but the console "
            f"rejected the settings ({detail})."
        )
    if status == PORT_BUSY:
        return (
            f"Port {port} is already in use on Home Assistant, so it can't receive the "
            "console's data. Choose a different Listener port and try again."
        )
    return "Home Assistant couldn't connect to the console's setup network."


class _ListenerPushWatcher:
    """Initial-setup watcher: bind a temporary listener to catch the first push.

    During first-time setup no entry (and so no listener) exists yet, so we stand
    one up on the chosen port just long enough to confirm the console reaches us.
    """

    def __init__(self, port: int) -> None:
        self._port = port
        self._event = asyncio.Event()
        self._ip: str | None = None
        self._listener: PushListener | None = None

    async def __aenter__(self) -> _ListenerPushWatcher:
        self._listener = PushListener(self._port, self._on_push)
        await self._listener.start()  # raises OSError if the port is busy
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._listener is not None:
            await self._listener.stop()
            self._listener = None

    @callback
    def _on_push(self, data: dict, source_ip: str | None) -> None:
        self._ip = source_ip
        self._event.set()

    async def wait(self, timeout: float) -> str | None:  # noqa: ASYNC109
        try:
            async with asyncio.timeout(timeout):
                await self._event.wait()
        except TimeoutError:
            return None
        return self._ip


class _CoordinatorPushWatcher:
    """Recovery watcher: the entry's listener already runs — watch for a new push.

    During re-provisioning the running entry already owns the port, so we can't
    bind a second listener; instead we watch the coordinator's push timestamp.
    """

    def __init__(self, coordinator) -> None:
        self._coord = coordinator
        self._baseline = None

    async def __aenter__(self) -> _CoordinatorPushWatcher:
        self._baseline = self._coord.last_push
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def wait(self, timeout: float) -> str | None:  # noqa: ASYNC109
        try:
            async with asyncio.timeout(timeout):
                while True:
                    last = self._coord.last_push
                    if last is not None and last != self._baseline:
                        return self._coord.console_ip
                    await asyncio.sleep(2)
        except TimeoutError:
            return None


class AmbientConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup (incl. provisioning a reset console in AP mode)."""

    VERSION = 1

    def __init__(self) -> None:
        self._pending: dict[str, Any] = {}
        self._iface: str | None = None
        self._ap_ssid: str | None = None
        self._console_ssids: list[str] = []
        self._cached: dict = {}
        self._reason: str = ""
        self._ha_ip_str: str | None = None
        self._result: ProvisionResult | None = None
        self._verify_task: asyncio.Task | None = None
        self._retry_ready: bool = False

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

        # Found it — join the AP and confirm the console answers.
        if not await join_and_reach(session, sup, self._iface, self._ap_ssid):
            await _release(sup, self._iface)
            return self._retry_form("provision", "ap_join_failed")

        # Ask the console which networks *it* can see, for the picker.
        ap_console = ConsoleClient(session, AP_HOST)
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
        """Collect Wi-Fi + name + port + email, then verify before committing."""
        self._cached = await _load_cache(self.hass)
        default_ssid = (self._cached.get("network") or {}).get("ssid") or ""
        default_email = (self._cached.get("ws") or {}).get("ambEmail") or ""

        if user_input is not None:
            self._pending = user_input
            self._verify_task = None
            return await self.async_step_verify()

        return self.async_show_form(
            step_id="setup",
            data_schema=self._setup_schema(default_ssid, default_email),
            description_placeholders={"ap": self._ap_ssid or "AMBWeatherPro-…"},
        )

    def _placeholders(self) -> dict:
        return {
            "ap": self._ap_ssid or "AMBWeatherPro-…",
            "ssid": self._pending.get("target_ssid", ""),
            "port": str(self._pending.get(CONF_LISTEN_PORT, DEFAULT_LISTEN_PORT)),
            "reason": self._reason,
            "ha_ip": self._ha_ip_str or "your Home Assistant's IP address",
        }

    async def _provision_and_verify(self) -> ProvisionResult:
        """Run the verified provisioning (off the event loop's critical path)."""
        session = async_get_clientsession(self.hass)
        sup = SupervisorNetwork(session)
        self._ha_ip_str = await self._ha_ip(sup)
        ws = {
            **(self._cached.get("ws") or {}),
            "ambEmail": self._pending.get("amb_email", ""),
        }
        cached = {**self._cached, "ws": ws}
        watcher = _ListenerPushWatcher(self._pending[CONF_LISTEN_PORT])
        try:
            return await provision_and_verify(
                session,
                sup,
                self._iface,
                self._ap_ssid,
                self._pending["target_ssid"],
                self._pending["target_psk"],
                cached,
                self._ha_ip_str,
                self._pending[CONF_LISTEN_PORT],
                watcher,
            )
        except OSError as err:  # temporary listener couldn't bind (port in use)
            await _release(sup, self._iface)
            return ProvisionResult(PORT_BUSY, detail=str(err))

    async def async_step_verify(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Progress step: wait for the console to come online, then route."""
        if self._verify_task is None:
            self._verify_task = self.hass.async_create_task(
                self._provision_and_verify()
            )
        if not self._verify_task.done():
            return self.async_show_progress(
                step_id="verify",
                progress_action="verify",
                description_placeholders=self._placeholders(),
                progress_task=self._verify_task,
            )
        try:
            result = self._verify_task.result()
        except Exception as err:
            _LOGGER.exception("Provisioning verification crashed")
            result = ProvisionResult(PROVISION_FAILED, detail=str(err))
        finally:
            self._verify_task = None

        self._result = result
        if result.status == OK:
            return self.async_show_progress_done(next_step_id="finish")
        if result.status == UNREACHABLE:
            return self.async_show_progress_done(next_step_id="unreachable")
        self._reason = _reason_text(
            result.status,
            self._pending.get("target_ssid", ""),
            self._pending.get(CONF_LISTEN_PORT, DEFAULT_LISTEN_PORT),
            result.detail,
        )
        self._retry_ready = False
        return self.async_show_progress_done(next_step_id="retry")

    async def async_step_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Verified working — commit the entry."""
        if self._result and self._result.console_ip:
            await _save_console_ip(self.hass, self._result.console_ip)
        mac = (self._cached.get("network") or {}).get("mac")
        if mac:
            await self.async_set_unique_id(mac.lower())
            self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=self._pending[CONF_DEVICE_NAME],
            data={
                CONF_DEVICE_NAME: self._pending[CONF_DEVICE_NAME],
                CONF_LISTEN_PORT: self._pending[CONF_LISTEN_PORT],
                CONF_SCAN_MINUTES: DEFAULT_SCAN_MINUTES,
            },
        )

    async def async_step_retry(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Recoverable failure — explain, then re-find the AP and try again.

        The progress step re-enters this step once carrying the original setup
        input; ``_retry_ready`` makes us show the form on that first pass and only
        re-provision on a genuine button press.
        """
        if self._retry_ready and user_input is not None:
            return await self.async_step_provision()
        self._retry_ready = True
        return self.async_show_form(
            step_id="retry",
            data_schema=vol.Schema({vol.Required("retry", default=True): bool}),
            description_placeholders=self._placeholders(),
        )

    async def async_step_unreachable(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Console left setup mode but can't reach us — nothing was committed."""
        return self.async_abort(
            reason="console_unreachable",
            description_placeholders=self._placeholders(),
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
    """Settings plus the AP-mode 'recover console' wizard (also verified)."""

    def __init__(self) -> None:
        self._iface: str | None = None
        self._ap_ssid: str | None = None
        self._candidates: list[str] = []
        self._pending: dict[str, Any] = {}
        self._reason: str = ""
        self._ha_ip_str: str | None = None
        self._result: ProvisionResult | None = None
        self._verify_task: asyncio.Task | None = None
        self._retry_ready: bool = False

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
        cached_ssid = (coord.cached.get("network") or {}).get("ssid") or ""

        if user_input is not None:
            self._pending = user_input
            self._verify_task = None
            return await self.async_step_verify()

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

    def _placeholders(self) -> dict:
        coord = self._coordinator()
        return {
            "ap": self._ap_ssid or "AMBWeatherPro-…",
            "ssid": self._pending.get("target_ssid", ""),
            "port": str(coord.listen_port),
            "reason": self._reason,
            "ha_ip": self._ha_ip_str or "your Home Assistant's IP address",
        }

    async def _provision_and_verify(self) -> ProvisionResult:
        coord = self._coordinator()
        session = async_get_clientsession(self.hass)
        sup = SupervisorNetwork(session)
        self._ha_ip_str = self._ha_ip(coord)
        if not await join_and_reach(session, sup, self._iface, self._ap_ssid):
            await _release(sup, self._iface)
            return ProvisionResult("ap_join_failed", detail="AP unreachable")
        return await provision_and_verify(
            session,
            sup,
            self._iface,
            self._ap_ssid,
            self._pending["target_ssid"],
            self._pending["target_psk"],
            coord.cached,
            self._ha_ip_str,
            coord.listen_port,
            _CoordinatorPushWatcher(coord),
        )

    async def async_step_verify(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if self._verify_task is None:
            self._verify_task = self.hass.async_create_task(
                self._provision_and_verify()
            )
        if not self._verify_task.done():
            return self.async_show_progress(
                step_id="verify",
                progress_action="verify",
                description_placeholders=self._placeholders(),
                progress_task=self._verify_task,
            )
        try:
            result = self._verify_task.result()
        except Exception as err:
            _LOGGER.exception("Recovery verification crashed")
            result = ProvisionResult(PROVISION_FAILED, detail=str(err))
        finally:
            self._verify_task = None

        self._result = result
        if result.status == OK:
            return self.async_show_progress_done(next_step_id="verified")
        if result.status == UNREACHABLE:
            return self.async_show_progress_done(next_step_id="unreachable")
        self._reason = _reason_text(
            result.status,
            self._pending.get("target_ssid", ""),
            self._coordinator().listen_port,
            result.detail,
        )
        self._retry_ready = False
        return self.async_show_progress_done(next_step_id="retry")

    async def async_step_verified(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_abort(
            reason="provision_done", description_placeholders=self._placeholders()
        )

    async def async_step_retry(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if self._retry_ready and user_input is not None:
            return await self.async_step_provision()
        self._retry_ready = True
        return self.async_show_form(
            step_id="retry",
            data_schema=vol.Schema({vol.Required("retry", default=True): bool}),
            description_placeholders=self._placeholders(),
        )

    async def async_step_unreachable(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_abort(
            reason="console_unreachable",
            description_placeholders=self._placeholders(),
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
