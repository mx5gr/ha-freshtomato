"""Sensor platform for FreshTomato integration.

Sensors provided (all from a single coordinator data snapshot):
──────────────────────────────────────────────────────────────────
Router / System
  • WAN IP Address          (per WAN – dynamically created)
  • WAN Gateway             (per WAN)
  • WAN Connection Type     (per WAN)
  • WAN DHCP Lease Remaining(per WAN)
  • WAN Uptime              (per WAN, disabled by default)
  • LAN IP Address
  • Firmware Version
  • Router Model

Network bandwidth (per WAN interface)
  • WAN Download (bytes total – from netdev)
  • WAN Upload   (bytes total – from netdev)

Multi-WAN: when mwan_num > 1 the above WAN sensors are created once per
active WAN (e.g. "WAN1 IP Address", "WAN2 IP Address", …).  Single-WAN
routers continue to show "WAN IP Address" as before.

Wi-Fi (per radio band – 2.4 GHz and 5 GHz)
  • SSID
  • Channel
  • Security Mode
  • Noise Floor (dBm)
  • Connected Clients (count)

Connected devices
  • Total Connected Devices
  • Wireless Devices
  • Wired Devices (DHCP)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    UnitOfDataRate,
    UnitOfInformation,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DATA_COORDINATOR, DOMAIN
from .coordinator import FreshTomatoCoordinator, RouterData
from .api import WanConnection


# ──────────────────────────────────────────────────────────────────────────────
# Entity descriptions
# ──────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True, kw_only=True)
class FreshTomatoSensorDescription(SensorEntityDescription):
    """Extends SensorEntityDescription with a value extraction function."""
    value_fn: Any = None  # Callable[[RouterData], Any]


def _wired_count(data: RouterData) -> int:
    """Wired devices = connected devices whose MACs are not wireless clients.

    Primary source: DHCP lease table (when this router runs a DHCP server).
    Fallback source: ARP table (when DHCP is handled upstream, e.g. in
    WET/bridge mode).  The ARP table always contains all recently-seen LAN
    hosts regardless of who issued their leases.
    """
    wireless_macs = {c["mac"] for c in data.wireless_clients}
    if data.dhcp_leases:
        wired = [l for l in data.dhcp_leases if l["mac"] not in wireless_macs]
    else:
        # No DHCP leases — router is likely in bridge/WET mode and not serving
        # DHCP itself.  Use the ARP table instead.
        wired = [e for e in data.arp_table if e["mac"] not in wireless_macs]
    return len(wired)


def _make_wan_sensor_descs(
    wan_idx: int,
    multi_wan: bool,
) -> tuple["FreshTomatoSensorDescription", ...]:
    """Return sensor descriptions for a single WAN interface.

    When multi_wan is False the legacy single-WAN key/name format is used
    (key="wan_ip", name="WAN IP Address") so existing entity IDs are preserved.
    When multi_wan is True labels are prefixed with "WAN<N>" so each WAN gets
    its own unique entity (key="wan1_ip", name="WAN1 IP Address", etc.).
    """
    prefix     = f"wan{wan_idx}_" if multi_wan else "wan_"
    label      = f"WAN{wan_idx} " if multi_wan else "WAN "
    # Capture wan_idx for use in closures
    _idx = wan_idx

    def _get_wan(data: RouterData) -> "WanConnection | None":
        for w in data.wan_connections:
            if w.index == _idx:
                return w
        return None

    return (
        FreshTomatoSensorDescription(
            key=f"{prefix}ip",
            name=f"{label}IP Address",
            icon="mdi:ip-network",
            value_fn=lambda d, _g=_get_wan: (_g(d).ip or None) if _g(d) else None,
        ),
        FreshTomatoSensorDescription(
            key=f"{prefix}gateway",
            name=f"{label}Gateway",
            icon="mdi:router-network",
            value_fn=lambda d, _g=_get_wan: (_g(d).gateway or None) if _g(d) else None,
        ),
        FreshTomatoSensorDescription(
            key=f"{prefix}proto",
            name=f"{label}Connection Type",
            icon="mdi:ethernet",
            value_fn=lambda d, _g=_get_wan: (_g(d).proto or None) if _g(d) else None,
        ),
        FreshTomatoSensorDescription(
            key=f"{prefix}dns",
            name="Router DNS",
            icon="mdi:dns",
            entity_registry_enabled_default=False,
            value_fn=lambda d, _g=_get_wan: (_g(d).dns or None) if _g(d) else None,
        ),
        FreshTomatoSensorDescription(
            key=f"{prefix}uptime",
            name=f"{label}Uptime",
            icon="mdi:timer-outline",
            native_unit_of_measurement=UnitOfTime.SECONDS,
            device_class=SensorDeviceClass.DURATION,
            state_class=SensorStateClass.MEASUREMENT,
            entity_registry_enabled_default=False,
            value_fn=lambda d, _g=_get_wan: (_g(d).uptime or None) if _g(d) else None,
        ),
        FreshTomatoSensorDescription(
            key=f"{prefix}lease",
            name=f"{label}DHCP Lease Remaining",
            icon="mdi:clock-outline",
            native_unit_of_measurement=UnitOfTime.SECONDS,
            device_class=SensorDeviceClass.DURATION,
            state_class=SensorStateClass.MEASUREMENT,
            entity_registry_enabled_default=False,
            value_fn=lambda d, _g=_get_wan: (_g(d).lease or None) if _g(d) else None,
        ),
    )

SYSTEM_SENSORS: tuple[FreshTomatoSensorDescription, ...] = (
    FreshTomatoSensorDescription(
        key="default_gateway",
        name="Router Default Gateway",
        icon="mdi:router-network",
        value_fn=lambda d: d.nvram.get("lan_gateway") or None,
    ),
    FreshTomatoSensorDescription(
        key="lan_ip",
        name="LAN IP Address",
        icon="mdi:lan",
        # Named dynamically in async_setup_entry using lan_ifname from nvram
        value_fn=lambda d: d.nvram.get("lan_ipaddr") or None,
    ),
    FreshTomatoSensorDescription(
        key="lan1_ip",
        name="LAN1 IP Address",
        icon="mdi:lan",
        # Named dynamically in async_setup_entry using lan1_ifname from nvram
        value_fn=lambda d: d.nvram.get("lan1_ipaddr") or None,
    ),
    FreshTomatoSensorDescription(
        key="firmware",
        name="Firmware Version",
        icon="mdi:package-up",
        value_fn=lambda d: (d.nvram.get("t_build_time") or d.nvram.get("os_version") or d.nvram.get("tomato_version") or d.nvram.get("t_build") or None),
    ),
    FreshTomatoSensorDescription(
        key="model",
        name="Router Model",
        icon="mdi:router-wireless",
        value_fn=lambda d: d.nvram.get("t_model_name") or None,
    ),
    FreshTomatoSensorDescription(
        key="cpu_load",
        name="CPU Load",
        icon="mdi:chip",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.cpu_load,
    ),
    FreshTomatoSensorDescription(
        key="ram_load",
        name="RAM Load",
        icon="mdi:memory",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.ram_load,
    ),
    FreshTomatoSensorDescription(
        key="system_uptime",
        name="System Uptime",
        icon="mdi:timer-outline",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.uptime_seconds,
    ),
    FreshTomatoSensorDescription(
        key="system_uptime_text",
        name="System Uptime (Text)",
        icon="mdi:timer-outline",
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.uptime_str,
    ),
    FreshTomatoSensorDescription(
        key="total_clients",
        name="Total Connected Devices",
        icon="mdi:devices",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: len(d.wireless_clients) + _wired_count(d),
    ),
    FreshTomatoSensorDescription(
        key="wireless_clients",
        name="Wireless Connected Devices",
        icon="mdi:wifi",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: len(d.wireless_clients),
    ),
    FreshTomatoSensorDescription(
        key="wired_clients",
        name="Wired Connected Devices",
        icon="mdi:ethernet",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_wired_count,
    ),
)

# Per-band Wi-Fi sensors – created dynamically in async_setup_entry
_WIFI_SENSOR_TEMPLATES: tuple[FreshTomatoSensorDescription, ...] = (
    FreshTomatoSensorDescription(
        key="ssid",
        name="SSID",
        icon="mdi:wifi",
        value_fn=None,  # set per-band
    ),
    FreshTomatoSensorDescription(
        key="channel",
        name="Channel",
        icon="mdi:access-point",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=None,
    ),
    FreshTomatoSensorDescription(
        key="security",
        name="Security Mode",
        icon="mdi:shield-lock",
        value_fn=None,
    ),
    FreshTomatoSensorDescription(
        key="noise",
        name="Noise Floor",
        icon="mdi:sine-wave",
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=None,
    ),
    FreshTomatoSensorDescription(
        key="mode",
        name="Mode",
        icon="mdi:access-point-network",
        value_fn=None,  # set per-band
    ),
)

# Per-interface bandwidth sensors
_BW_SENSOR_TEMPLATES: tuple[FreshTomatoSensorDescription, ...] = (
    FreshTomatoSensorDescription(
        key="rx_bytes",
        name="Download (total)",
        icon="mdi:download-network",
        native_unit_of_measurement=UnitOfInformation.BYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=None,
    ),
    FreshTomatoSensorDescription(
        key="tx_bytes",
        name="Upload (total)",
        icon="mdi:upload-network",
        native_unit_of_measurement=UnitOfInformation.BYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=None,
    ),
)


# ──────────────────────────────────────────────────────────────────────────────
# Platform setup
# ──────────────────────────────────────────────────────────────────────────────

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: FreshTomatoCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    entities: list[FreshTomatoSensor] = []
    nvram = coordinator.data.nvram if coordinator.data else {}

    # ── Bridge mode detection ─────────────────────────────────────────────
    # When wan_proto is "disabled" but lan1_ipaddr is a real IP, the router is
    # operating as a Wireless Ethernet Bridge (WET).  The "WAN" IP/gateway
    # actually belong to the bridge interface (br1), not a true WAN port.
    # We rename WAN-labelled entities to "Bridge <ifname> …" in this case.
    # The entity *keys* are kept unchanged to preserve existing entity IDs.
    _wan_proto_main = nvram.get("wan_proto", "")
    _lan1_ip        = nvram.get("lan1_ipaddr", "")
    _bridge_mode    = (
        _wan_proto_main in ("disabled", "")
        and bool(_lan1_ip) and _lan1_ip not in ("", "0.0.0.0")
    )
    # bridge_ifname is the LAN bridge carrying the WET uplink (e.g. "br1")
    _bridge_ifname  = nvram.get("lan1_ifname", "br1") or "br1"
    # bridge_iface is the physical/VLAN interface for bandwidth measurement
    # lan1_ifnames example: "vlan3 eth1" → use the first token
    _bridge_bw_iface = (nvram.get("lan1_ifnames", "") or "").split()[0] or _bridge_ifname

    # ── WAN sensors (per-WAN for multi-WAN; legacy single for single-WAN) ──
    # mwan_num tells us how many WAN ports are configured; default is 1.
    wan_connections = coordinator.data.wan_connections if coordinator.data else []
    # Determine whether the router is actually running in multi-WAN mode:
    # mwan_num > 1 in NVRAM  OR  more than one active WAN connection detected.
    try:
        _mwan_num = int(nvram.get("mwan_num", "1"))
    except ValueError:
        _mwan_num = 1
    multi_wan = _mwan_num > 1 or len(wan_connections) > 1

    # Name-override map: sensor key → display name, applied in bridge mode
    _bridge_name_overrides: dict[str, str] = {
        "wan_ip":      f"{_bridge_ifname} Interface IP Address",
        "wan_gateway": f"{_bridge_ifname} Gateway",
        "wan_dns":     "Router DNS",
    } if _bridge_mode else {}

    # Keys to suppress entirely in bridge mode (value is always "disabled" or misleading)
    _bridge_skip_keys: frozenset[str] = frozenset({"wan_proto"}) if _bridge_mode else frozenset()

    if multi_wan:
        # One set of WAN sensors per active WAN connection
        active_indices = [w.index for w in wan_connections] if wan_connections else [1]
        for wan_idx in active_indices:
            wan_proto = (
                next((w.proto for w in wan_connections if w.index == wan_idx), "")
            )
            for desc in _make_wan_sensor_descs(wan_idx, multi_wan=True):
                if desc.key.endswith(("_uptime", "_lease")) and wan_proto in ("disabled", ""):
                    continue
                entities.append(FreshTomatoSensor(coordinator, entry, desc))
    else:
        # Single-WAN: use legacy key names to preserve existing entity IDs
        wan_proto = nvram.get("wan_proto", "")
        for desc in _make_wan_sensor_descs(1, multi_wan=False):
            if desc.key in ("wan_uptime", "wan_lease") and wan_proto in ("disabled", ""):
                continue
            # In bridge mode suppress sensors that are always wrong/meaningless
            if desc.key in _bridge_skip_keys:
                continue
            # Apply bridge-mode name overrides
            override_name = _bridge_name_overrides.get(desc.key)
            if override_name:
                desc = FreshTomatoSensorDescription(
                    key=desc.key,
                    name=override_name,
                    icon=desc.icon,
                    entity_registry_enabled_default=desc.entity_registry_enabled_default,
                    native_unit_of_measurement=desc.native_unit_of_measurement,
                    device_class=desc.device_class,
                    state_class=desc.state_class,
                    value_fn=desc.value_fn,
                )
            entities.append(FreshTomatoSensor(coordinator, entry, desc))

    # ── System sensors ────────────────────────────────────────────────────
    wan_proto = nvram.get("wan_proto", "")
    for desc in SYSTEM_SENSORS:
        if desc.key == "lan_ip":
            ifname = nvram.get("lan_ifname", "br0") or "br0"
            desc = FreshTomatoSensorDescription(
                key=desc.key,
                name=f"{ifname} IP Address",
                icon="mdi:lan",
                value_fn=desc.value_fn,
            )
        elif desc.key == "lan1_ip":
            ifname = nvram.get("lan1_ifname", "br1") or "br1"
            if not nvram.get("lan1_ipaddr"):
                continue  # Skip if no secondary bridge
            desc = FreshTomatoSensorDescription(
                key=desc.key,
                name=f"{ifname} IP Address",
                icon="mdi:lan",
                value_fn=desc.value_fn,
            )
        entities.append(FreshTomatoSensor(coordinator, entry, desc))


    # ── Per-band Wi-Fi sensors ────────────────────────────────────────────
    for band_label, band_idx in [("2.4 GHz", 0), ("5 GHz", 1)]:
        for tmpl in _WIFI_SENSOR_TEMPLATES:
            vfn = _make_wifi_value_fn(tmpl.key, band_idx)
            entities.append(FreshTomatoSensor(coordinator, entry,
                FreshTomatoSensorDescription(
                    key=f"wl{band_idx}_{tmpl.key}",
                    name=f"{band_label} {tmpl.name}",
                    icon=tmpl.icon,
                    native_unit_of_measurement=tmpl.native_unit_of_measurement,
                    device_class=tmpl.device_class,
                    state_class=tmpl.state_class,
                    value_fn=vfn,
                )
            ))

    # ── WAN bandwidth sensors ─────────────────────────────────────────────
    # For single-WAN use the legacy "WAN Download/Upload" key names.
    # For multi-WAN create one pair per active WAN, named "WAN1 Download", etc.
    if multi_wan:
        for wan_idx in (active_indices if "active_indices" in dir() else [1]):
            wan_ifname = next(
                (w.ifname for w in wan_connections if w.index == wan_idx), "vlan2"
            ) or "vlan2"
            for tmpl in _BW_SENSOR_TEMPLATES:
                key_suffix = "rx" if "rx" in tmpl.key else "tx"
                entities.append(FreshTomatoSensor(coordinator, entry,
                    FreshTomatoSensorDescription(
                        key=f"wan{wan_idx}_{tmpl.key}",
                        name=f"WAN{wan_idx} {tmpl.name}",
                        icon=tmpl.icon,
                        native_unit_of_measurement=tmpl.native_unit_of_measurement,
                        device_class=tmpl.device_class,
                        state_class=tmpl.state_class,
                        value_fn=_make_netdev_value_fn(wan_ifname, key_suffix),
                    )
                ))
    else:
        # Single-WAN (or bridge mode): bandwidth measured on the uplink interface.
        # In bridge mode vlan2 carries no meaningful traffic — use the WET
        # bridge's physical interface (e.g. eth1 from lan1_ifnames) instead.
        _bw_iface = _bridge_bw_iface if _bridge_mode else "vlan2"
        _bw_label = f"{_bridge_ifname}" if _bridge_mode else "WAN"
        for tmpl in _BW_SENSOR_TEMPLATES:
            key_suffix = "rx" if "rx" in tmpl.key else "tx"
            entities.append(FreshTomatoSensor(coordinator, entry,
                FreshTomatoSensorDescription(
                    key=f"wan_{tmpl.key}",
                    name=f"{_bw_label} {tmpl.name}",
                    icon=tmpl.icon,
                    native_unit_of_measurement=tmpl.native_unit_of_measurement,
                    device_class=tmpl.device_class,
                    state_class=tmpl.state_class,
                    value_fn=_make_netdev_value_fn(_bw_iface, key_suffix),
                )
            ))

    async_add_entities(entities)

    # ── Dynamic per-port speed sensors ────────────────────────────────────
    # Created from eth_ports which is populated by etherstates in devlist.
    # The listener fires on every coordinator update, so new ports discovered
    # after initial setup (e.g. after router reboot) are added automatically.
    known_ports: set[str] = set()

    def _add_port_sensors() -> None:
        if not coordinator.data:
            return
        new: list[FreshTomatoPortSensor] = []
        for label in coordinator.data.eth_ports:
            if label not in known_ports:
                known_ports.add(label)
                new.append(FreshTomatoPortSensor(coordinator, entry, label))
        if new:
            async_add_entities(new)

    _add_port_sensors()
    entry.async_on_unload(coordinator.async_add_listener(_add_port_sensors))


# ──────────────────────────────────────────────────────────────────────────────
# Value function factories
# ──────────────────────────────────────────────────────────────────────────────

def _make_wifi_value_fn(key: str, band_idx: int):
    """Create a value function for a specific Wi-Fi band sensor."""
    _MODE_LABELS = {
        "ap": "Access Point",
        "sta": "Wireless Client",
        "wet": "Wireless Ethernet Bridge",
        "wds": "WDS",
        "psta": "Media Bridge",
        "apsta": "AP + Client",
    }
    nvram_key_map = {
        "ssid": f"wl{band_idx}_ssid",
        "channel": f"wl{band_idx}_channel",
        "security": f"wl{band_idx}_security_mode",
        "mode": f"wl{band_idx}_mode",
    }

    def _fn(data: RouterData) -> Any:
        if key == "noise":
            if data.wl_noise and band_idx < len(data.wl_noise):
                v = data.wl_noise[band_idx]
                return v if v != 0 else None
            return None
        nvk = nvram_key_map.get(key)
        if nvk:
            raw_val = data.nvram.get(nvk) or None
            if key == "mode" and raw_val:
                return _MODE_LABELS.get(raw_val, raw_val)
            return raw_val
        return None

    return _fn


def _make_netdev_value_fn(fallback_iface: str, direction: str):
    """Create a value function for a WAN/uplink bandwidth sensor.

    Tries interfaces in priority order:
      1. nvram wan_ifname / wan_ifnames (explicit WAN interface)
      2. wan_iface (alternate nvram key used by some builds)
      3. br0 (LAN bridge — carries all traffic in bridge/WEB mode)
      4. fallback_iface (hardcoded default, usually vlan2)

    In wireless bridge (WET) mode wan_proto is "disabled" and traffic
    flows through the LAN bridge br0, not a dedicated WAN interface.
    """
    def _fn(data: RouterData) -> int | None:
        candidates: list[str] = []
        # Primary: explicit nvram WAN interface names
        for key in ("wan_ifname", "wan_ifnames", "wan_iface"):
            v = data.nvram.get(key, "").strip().split()[0] if data.nvram.get(key, "").strip() else ""
            if v and v not in candidates:
                candidates.append(v)
        # Bridge/WET mode fallback: br0 carries uplink traffic
        for bridge in ("br0", "br1"):
            if bridge not in candidates:
                candidates.append(bridge)
        # Last resort
        if fallback_iface not in candidates:
            candidates.append(fallback_iface)

        for iface in candidates:
            counters = data.netdev.get(iface)
            if counters and counters.get(direction, 0) > 0:
                return counters.get(direction, 0)
        # Return 0 from first valid interface even if zero (avoids None for active iface)
        for iface in candidates:
            counters = data.netdev.get(iface)
            if counters is not None:
                return counters.get(direction, 0)
        return None
    return _fn


# ──────────────────────────────────────────────────────────────────────────────
# Entity class
# ──────────────────────────────────────────────────────────────────────────────

class FreshTomatoSensor(CoordinatorEntity[FreshTomatoCoordinator], SensorEntity):
    """A sensor entity for a FreshTomato router metric."""

    entity_description: FreshTomatoSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: FreshTomatoCoordinator,
        entry: ConfigEntry,
        description: FreshTomatoSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._entry = entry

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name=f"FreshTomato ({self._entry.data['host']})",
            manufacturer="FreshTomato Project",
            model=self.coordinator.data.nvram.get("t_model_name", "Router"),
            sw_version=(self.coordinator.data.nvram.get("t_build_time") or self.coordinator.data.nvram.get("os_version")),
        )

    @property
    def available(self) -> bool:
        if not super().available or self.coordinator.data is None:
            return False
        # WAN uptime and lease only exist when the router has a live WAN
        # connection. In bridge/AP mode (wan_proto=disabled or empty) these
        # values are never populated. Return False so HA shows "Unavailable"
        # rather than "Unknown" — a clear signal the sensor doesn't apply.
        if self.entity_description.key in ("wan_uptime", "wan_lease"):
            proto = self.coordinator.data.nvram.get("wan_proto", "")
            if proto in ("disabled", ""):
                return False
        return True

    @property
    def native_value(self) -> Any:
        if self.coordinator.data is None:
            return None
        fn = self.entity_description.value_fn
        if fn is None:
            return None
        try:
            return fn(self.coordinator.data)
        except Exception:  # pylint: disable=broad-except
            return None

    @property
    def extra_state_attributes(self) -> dict:
        """Expose device lists for the three client-count sensors.

        For wireless, wired, and total sensors only — other sensors return {}.
        Wired list falls back to arp_table when dhcp_leases is empty (bridge mode).
        """
        if self.coordinator.data is None:
            return {}
        key = self.entity_description.key
        if key not in ("wireless_clients", "wired_clients", "total_clients"):
            return {}

        data = self.coordinator.data
        wireless_macs = {c["mac"] for c in data.wireless_clients}

        # Build wireless device list
        wireless_list = [
            {
                "mac": c["mac"],
                "ip": next(
                    (a["ip"] for a in data.arp_table if a["mac"] == c["mac"]), None
                ),
                "name": next(
                    (
                        l["name"] for l in data.dhcp_leases
                        if l["mac"] == c["mac"] and l.get("name") and l["name"] != "*"
                    ),
                    next((a["name"] for a in data.arp_table if a["mac"] == c["mac"] and a.get("name")), None),
                ),
                "interface": c.get("iface"),
                "rssi": c.get("rssi"),
            }
            for c in data.wireless_clients
        ]

        # Build wired device list — DHCP leases preferred, ARP table fallback
        if data.dhcp_leases:
            wired_list = [
                {
                    "mac": l["mac"],
                    "ip": l.get("ip"),
                    "name": l["name"] if l.get("name") and l["name"] != "*" else None,
                }
                for l in data.dhcp_leases
                if l["mac"] not in wireless_macs
            ]
        else:
            wired_list = [
                {
                    "mac": e["mac"],
                    "ip": e.get("ip"),
                    "name": e["name"] if e.get("name") else None,
                }
                for e in data.arp_table
                if e["mac"] not in wireless_macs
            ]

        if key == "wireless_clients":
            return {"devices": wireless_list}
        if key == "wired_clients":
            return {"devices": wired_list}
        # total_clients
        return {"devices": wireless_list + wired_list}


# ──────────────────────────────────────────────────────────────────────────────
# Ethernet port sensor
# ──────────────────────────────────────────────────────────────────────────────

# Maps raw etherstates strings to friendly display values
# Maps raw FreshTomato port state strings to human-readable labels
_PORT_STATE_MAP: dict[str, str] = {
    "DOWN":     "Disconnected",
    "ACTIVE":   "Connected",
    "10HD":     "10 Mbps, Half Duplex",
    "10FD":     "10 Mbps, Full Duplex",
    "100HD":    "100 Mbps, Half Duplex",
    "100FD":    "100 Mbps, Full Duplex",
    "1000HD":   "1 Gbps, Half Duplex",
    "1000FD":   "1 Gbps, Full Duplex",
    "2500FD":   "2.5 Gbps, Full Duplex",
    "5000FD":   "5 Gbps, Full Duplex",
    "10000FD":  "10 Gbps, Full Duplex",
}

# Maps raw state to speed in Mbps (None if disconnected or speed unknown)
_PORT_SPEED_MAP: dict[str, int | None] = {
    "10HD": 10, "10FD": 10,
    "100HD": 100, "100FD": 100,
    "1000HD": 1000, "1000FD": 1000,
    "2500FD": 2500, "5000FD": 5000, "10000FD": 10000,
}


def _port_speed_mbps(raw: str) -> int | None:
    """Extract numeric speed in Mbps from raw port state string."""
    return _PORT_SPEED_MAP.get(raw)


def _port_duplex(raw: str) -> str | None:
    if raw.endswith("FD"):
        return "full"
    if raw.endswith("HD"):
        return "half"
    return None


def _port_name_from_data(data: RouterData, port_label: str) -> str | None:
    """Look up the hostname of the device connected to a LAN port.

    Matches the ARP table entry for the port's IP against the DHCP lease
    table to find a hostname. Returns None for WAN port or if unknown.
    """
    if port_label == "WAN":
        return None
    # Build a quick MAC→name lookup from DHCP leases
    mac_to_name: dict[str, str] = {
        lease["mac"]: lease["name"]
        for lease in data.dhcp_leases
        if lease.get("name") and lease["name"] not in ("*", "")
    }
    # ARP table has iface info but FreshTomato doesn't map port# to iface
    # reliably, so we can't do a per-port lookup. Return total DHCP names
    # as a hint only for the attributes dict.
    return None  # populated per-attribute below, not as sensor state


class FreshTomatoPortSensor(CoordinatorEntity[FreshTomatoCoordinator], SensorEntity):
    """Sensor for a single Ethernet port's link speed / status.

    State: human-readable speed string (e.g. "1 Gbps", "100 Mbps", "Disconnected")
    Attributes:
      - speed_mbps       numeric speed, None when disconnected
      - duplex           "full" | "half" | None
      - raw_state        raw firmware string ("1000FD", "DOWN", …)
      - connected_hosts  list of hostnames seen on this port segment (LAN only)
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:ethernet"

    def __init__(
        self,
        coordinator: FreshTomatoCoordinator,
        entry: ConfigEntry,
        port_label: str,
    ) -> None:
        super().__init__(coordinator)
        self._port_label = port_label
        self._entry = entry
        safe = port_label.lower().replace(" ", "_")
        self._attr_unique_id = f"{entry.entry_id}_port_{safe}_speed"
        self._attr_name = f"{port_label} Port"
        self._attr_icon = "mdi:wan" if port_label == "WAN" else "mdi:ethernet"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name=f"FreshTomato ({self._entry.data['host']})",
            manufacturer="FreshTomato Project",
            model=self.coordinator.data.nvram.get("t_model_name", "Router")
            if self.coordinator.data else "Router",
            sw_version=(self.coordinator.data.nvram.get("t_build_time") or self.coordinator.data.nvram.get("os_version"))
            if self.coordinator.data else None,
        )

    @property
    def native_value(self) -> str | None:
        if not self.coordinator.data:
            return None
        raw = self.coordinator.data.eth_ports.get(self._port_label)
        if raw is None:
            return None
        return _PORT_STATE_MAP.get(raw, raw)

    @property
    def extra_state_attributes(self) -> dict:
        if not self.coordinator.data:
            return {}
        data = self.coordinator.data
        raw = data.eth_ports.get(self._port_label, "")
        attrs: dict = {
            "raw_state": raw,
            "speed_mbps": _port_speed_mbps(raw),
            "duplex": _port_duplex(raw),
        }

        # For LAN ports: list hostnames of all DHCP clients on the LAN segment.
        # FreshTomato doesn't expose per-port MAC binding in etherstates, so
        # we list all known DHCP hostnames as context (useful for WAN too —
        # shows the WAN gateway hostname if resolvable).
        if self._port_label != "WAN":
            names = [
                lease["name"]
                for lease in data.dhcp_leases
                if lease.get("name") and lease["name"] not in ("*", "")
            ]
            attrs["connected_hosts"] = sorted(set(names)) if names else []

        return attrs
