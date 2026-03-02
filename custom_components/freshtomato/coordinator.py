"""Data coordinator for FreshTomato integration.

Design goal: MINIMUM HTTP calls per poll cycle.
─────────────────────────────────────────────────
Cycle (every scan_interval seconds):
  Call 1 – POST /update.cgi  exec=devlist
      → wldev, wlnoise, dhcpd_lease, arplist, wanip/gateway/netmask/uptime/lease

  Call 2 – POST /update.cgi  exec=netdev
      → netdev (per-interface byte counters)

Every NVRAM_REFRESH_INTERVAL cycles (~5 min):
  Call 3 – POST /update.cgi  exec=nvram  (or GET status-overview.asp fallback)
      → firmware, model, SSID, radio state, WAN proto, LAN IP, etc.

Total: 2 calls/cycle + 1 call every ~5 min.

Wireless client / repeater mode:
  When the router operates as a wireless client (no WAN IP, DHCP server
  disabled), WAN sensors return None and the device-list is sourced from
  the wldev table only (no dhcpd_lease). This is detected automatically.
"""
from __future__ import annotations

import logging
import re
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import CannotConnect, FreshTomatoAPI, InvalidAuth, RouterData
from .const import (
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    NVRAM_REFRESH_INTERVAL,
    NVRAM_VARS,
)

_LOGGER = logging.getLogger(__name__)


class FreshTomatoCoordinator(DataUpdateCoordinator[RouterData]):
    """Coordinator: fetches all FreshTomato data and distributes to platforms."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: FreshTomatoAPI,
        entry: ConfigEntry,
        scan_interval: int = DEFAULT_SCAN_INTERVAL,
    ) -> None:
        self.api = api
        self._entry = entry
        self._cycle_count = 0
        self._nvram_cache: dict[str, str] = {}
        self._nvram_supported: bool | None = None
        # Detected router operating mode
        self.wireless_client_mode: bool = False

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} ({entry.data.get('host', 'router')})",
            update_interval=timedelta(seconds=scan_interval),
        )

    async def _async_update_data(self) -> RouterData:
        self._cycle_count += 1
        data = RouterData()

        try:
            devlist_raw = await self.api.fetch_devlist()
            _parse_devlist(devlist_raw, data)

            # The devlist response embeds a full nvram dict every cycle.
            # Merge it into the cache immediately — this is the most reliable
            # source for wl0_mode, wan_ipaddr, wan_ifname, etc. and it arrives
            # on every poll without a separate HTTP call.
            inline_nvram = devlist_raw.get("nvram", {})
            if isinstance(inline_nvram, dict):
                self._nvram_cache.update({k: str(v) for k, v in inline_nvram.items() if v != ""})

            # Debug: log etherstates parse result so we can diagnose port issues
            _LOGGER.debug(
                "etherstates raw=%r  eth_ports=%r",
                devlist_raw.get("etherstates"),
                data.eth_ports,
            )

            netdev_raw = await self.api.fetch_netdev()
            _parse_netdev(netdev_raw, data)

            # Fetch physical port states — exec=etherstates is a separate call;
            # this router does not embed etherstates in the devlist response.
            etherstates = await self.api.fetch_etherstates()
            _parse_etherstates_dict(etherstates, data)
            _parse_netdev(netdev_raw, data)

            if self._cycle_count == 1 or (self._cycle_count % NVRAM_REFRESH_INTERVAL == 0):
                await self._refresh_nvram()

        except InvalidAuth as err:
            raise UpdateFailed(f"Authentication failed: {err}") from err
        except CannotConnect as err:
            raise UpdateFailed(f"Cannot connect to router: {err}") from err

        data.nvram = dict(self._nvram_cache)

        # WAN scalars — populated from inline nvram merged into cache above
        data.wan_ip      = data.nvram.get("wan_ipaddr", "").strip()
        data.wan_netmask = data.nvram.get("wan_netmask", "").strip()
        data.wan_gateway = (data.nvram.get("wan_gateway_get") or data.nvram.get("wan_gateway", "")).strip()

        # Detect wireless client / repeater / bridge mode
        wan_proto = data.nvram.get("wan_proto", "")
        self.wireless_client_mode = (
            wan_proto in ("disabled", "") or
            (not data.wan_ip or data.wan_ip in ("", "0.0.0.0"))
        )

        return data

    async def _refresh_nvram(self) -> None:
        if self._nvram_supported is not False:
            try:
                result = await self.api.fetch_nvram(NVRAM_VARS)
                if result:
                    self._nvram_cache.update(result)
                    self._nvram_supported = True
                    return
                self._nvram_supported = False
            except Exception:  # pylint: disable=broad-except
                self._nvram_supported = False

        try:
            result = await self.api.fetch_nvram_from_asp(NVRAM_VARS)
            if result:
                self._nvram_cache.update(result)
        except CannotConnect as err:
            _LOGGER.warning("NVRAM ASP fallback failed: %s", err)

        # Firmware version is not exposed via exec=nvram on all builds.
        # Fall back to parsing it from the about.asp / status-overview.asp HTML.
        if not any(k in self._nvram_cache for k in ("t_build_time", "os_version", "tomato_version")):
            fw = await self.api.fetch_about_page()
            _LOGGER.debug("fetch_about_page returned: %r", fw)
            if fw:
                self._nvram_cache["t_build_time"] = fw
        _LOGGER.debug(
            "nvram firmware keys: t_build_time=%r os_version=%r tomato_version=%r",
            self._nvram_cache.get("t_build_time"),
            self._nvram_cache.get("os_version"),
            self._nvram_cache.get("tomato_version"),
        )


# ── Parsing helpers ───────────────────────────────────────────────────────────

def _parse_devlist(raw: dict[str, Any], data: RouterData) -> None:
    """Parse all fields from exec=devlist response.

    Actual FreshTomato devlist response keys (verified from live logs):
      wldev       – flat list of client entries OR nested per-radio list
                    flat entry:   [iface, mac, rssi, tx_rate, rx_rate, uptime, assoc]
                    nested entry: [[mac, rssi, tx_rate, rx_rate, iface, uptime, assoc], ...]
      wlnoise     – [noise_radio0, noise_radio1] (dBm integers)
      dhcpd_lease – [[name, mac, ip, ttl_sec], ...]
      arplist     – [[ip, mac, iface, name], ...]
      nvram       – dict of nvram key/value pairs (includes WAN data)
      etherstates – dict of port states (already parsed by JS parser)
      gc_time     – int (ignored)
    """

    # Wireless clients — handle both flat and nested-per-radio formats
    # Flat:   [iface, mac, rssi, tx, rx, uptime, assoc]  (iface is a string like 'eth1')
    # Nested: [[mac, rssi, tx, rx, iface, uptime, assoc], ...] per radio
    wldev_raw = raw.get("wldev", [])
    if isinstance(wldev_raw, list):
        for entry in wldev_raw:
            if not isinstance(entry, list):
                continue
            # Detect format: flat if first element is a string interface name
            if entry and isinstance(entry[0], str) and not _looks_like_mac(str(entry[0])):
                # Flat format: [iface, mac, rssi, tx_rate, rx_rate, uptime, assoc]
                if len(entry) < 2:
                    continue
                mac = str(entry[1]).upper().strip()
                if not mac or mac == "00:00:00:00:00:00":
                    continue
                data.wireless_clients.append({
                    "mac": mac,
                    "rssi": _safe_int(entry[2]) if len(entry) > 2 else None,
                    "tx_rate": _safe_int(entry[3]) if len(entry) > 3 else None,
                    "rx_rate": _safe_int(entry[4]) if len(entry) > 4 else None,
                    "iface": str(entry[0]).strip(),
                })
            elif entry and isinstance(entry[0], list):
                # Nested per-radio format: [[client, ...], ...]
                for client in entry:
                    if not isinstance(client, list) or len(client) < 1:
                        continue
                    mac = str(client[0]).upper().strip()
                    if not mac or mac == "00:00:00:00:00:00":
                        continue
                    data.wireless_clients.append({
                        "mac": mac,
                        "rssi": _safe_int(client[1]) if len(client) > 1 else None,
                        "tx_rate": _safe_int(client[2]) if len(client) > 2 else None,
                        "rx_rate": _safe_int(client[3]) if len(client) > 3 else None,
                        "iface": str(client[4]).strip() if len(client) > 4 else "",
                    })
            else:
                # Single flat client entry (mac first)
                mac = str(entry[0]).upper().strip()
                if not mac or mac == "00:00:00:00:00:00":
                    continue
                data.wireless_clients.append({
                    "mac": mac,
                    "rssi": _safe_int(entry[1]) if len(entry) > 1 else None,
                    "tx_rate": _safe_int(entry[2]) if len(entry) > 2 else None,
                    "rx_rate": _safe_int(entry[3]) if len(entry) > 3 else None,
                    "iface": str(entry[4]).strip() if len(entry) > 4 else "",
                })

    # Noise floors
    wlnoise_raw = raw.get("wlnoise", [])
    if isinstance(wlnoise_raw, list):
        data.wl_noise = [_safe_int(n) for n in wlnoise_raw]

    # DHCP leases
    for entry in raw.get("dhcpd_lease", []) or []:
        if not isinstance(entry, list) or len(entry) < 3:
            continue
        data.dhcp_leases.append({
            "name": str(entry[0]).strip(),
            "mac": str(entry[1]).upper().strip(),
            "ip": str(entry[2]).strip(),
            "lease": _safe_int(entry[3]) if len(entry) > 3 else 0,
        })

    # ARP table
    for entry in (raw.get("arplist") or raw.get("arp") or []):
        if not isinstance(entry, list) or len(entry) < 2:
            continue
        data.arp_table.append({
            "ip": str(entry[0]).strip(),
            "mac": str(entry[1]).upper().strip(),
            "iface": str(entry[2]).strip() if len(entry) > 2 else "",
            "name": str(entry[3]).strip() if len(entry) > 3 else "",
        })

    # WAN data comes from inline nvram merged into _nvram_cache in _async_update_data.
    # data.wan_ip/gateway are set below after data.nvram is assigned.
    # (eth_ports is populated separately via fetch_etherstates in _async_update_data)


def _parse_etherstates_dict(es: dict[str, str], data: RouterData) -> None:
    """Populate data.eth_ports from the clean {portN: state} dict returned
    by api.fetch_etherstates().

    Port → label mapping (standard Broadcom, EA6700 confirmed):
        port0 = WAN
        port1 = LAN 0  (FreshTomato UI uses 0-indexed LAN labels)
        port2 = LAN 1
        port3 = LAN 2
        port4 = LAN 3
    Ports absent on this hardware are returned as "disabled" and skipped.
    """
    _PORT_LABELS: dict[str, str] = {
        "port0": "WAN",
        "port1": "LAN0",
        "port2": "LAN1",
        "port3": "LAN2",
        "port4": "LAN3",
    }
    for port_key, state in es.items():
        if state in ("disabled", ""):
            continue
        label = _PORT_LABELS.get(port_key, port_key.replace("port", "Port "))
        data.eth_ports[label] = state


def _parse_netdev(raw: dict[str, Any], data: RouterData) -> None:
    """Parse netdev byte counters.

    FreshTomato netdev response:
        netdev = {'eth0':{'rx':123,'tx':456,'rxp':12,'txp':34}, ...}
    Keys are interface names; values are dicts with rx/tx byte counters.
    """
    netdev_raw = raw.get("netdev", {})
    if not isinstance(netdev_raw, dict):
        return
    for iface, counters in netdev_raw.items():
        if not isinstance(counters, dict):
            continue
        data.netdev[str(iface)] = {
            "rx": _safe_int(counters.get("rx", 0)),
            "tx": _safe_int(counters.get("tx", 0)),
            "rxp": _safe_int(counters.get("rxp", 0)),
            "txp": _safe_int(counters.get("txp", 0)),
        }



def _looks_like_mac(s: str) -> bool:
    """Return True if s looks like a MAC address (AA:BB:CC:DD:EE:FF)."""
    return bool(re.match(r'^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$', s))


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
