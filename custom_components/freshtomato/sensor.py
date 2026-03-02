"""Sensor platform for FreshTomato integration.

Sensors provided (all from a single coordinator data snapshot):
──────────────────────────────────────────────────────────────────
Router / System
  • WAN IP Address
  • WAN Gateway
  • WAN Connection Type (proto)
  • WAN DHCP Lease Remaining (seconds → human)
  • WAN Uptime (seconds since WAN connected)
  • LAN IP Address
  • Firmware Version
  • Router Model

Network bandwidth (per WAN interface)
  • WAN Download (bytes total – from netdev)
  • WAN Upload (bytes total – from netdev)

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


# ──────────────────────────────────────────────────────────────────────────────
# Entity descriptions
# ──────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True, kw_only=True)
class FreshTomatoSensorDescription(SensorEntityDescription):
    """Extends SensorEntityDescription with a value extraction function."""
    value_fn: Any = None  # Callable[[RouterData], Any]


def _wired_count(data: RouterData) -> int:
    """Wired devices = DHCP leases whose MACs are not in the wireless list."""
    wireless_macs = {c["mac"] for c in data.wireless_clients}
    wired = [l for l in data.dhcp_leases if l["mac"] not in wireless_macs]
    return len(wired)


WAN_SENSORS: tuple[FreshTomatoSensorDescription, ...] = (
    FreshTomatoSensorDescription(
        key="wan_ip",
        name="WAN IP Address",
        icon="mdi:ip-network",
        value_fn=lambda d: d.wan_ip or None,
    ),
    FreshTomatoSensorDescription(
        key="wan_gateway",
        name="WAN Gateway",
        icon="mdi:router-network",
        value_fn=lambda d: d.wan_gateway or None,
    ),
    FreshTomatoSensorDescription(
        key="wan_proto",
        name="WAN Connection Type",
        icon="mdi:ethernet",
        value_fn=lambda d: d.nvram.get("wan_proto") or None,
    ),
    FreshTomatoSensorDescription(
        key="wan_uptime",
        name="WAN Uptime",
        icon="mdi:timer-outline",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.wan_uptime if d.wan_uptime else None,
    ),
    FreshTomatoSensorDescription(
        key="wan_lease",
        name="WAN DHCP Lease Remaining",
        icon="mdi:clock-outline",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.wan_lease if d.wan_lease else None,
    ),
)

SYSTEM_SENSORS: tuple[FreshTomatoSensorDescription, ...] = (
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
        key="total_clients",
        name="Total Connected Devices",
        icon="mdi:devices",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: len({c["mac"] for c in d.wireless_clients}
                                | {l["mac"] for l in d.dhcp_leases}),
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

    # ── Static WAN + System sensors ───────────────────────────────────────
    # Bridge IP sensors (lan_ip, lan1_ip) are given dynamic names using the
    # actual bridge interface name from nvram (e.g. "br0 IP", "br1 IP").
    nvram = coordinator.data.nvram if coordinator.data else {}
    wan_proto = nvram.get("wan_proto", "")
    for desc in WAN_SENSORS + SYSTEM_SENSORS:
        # Skip WAN uptime and lease when WAN is disabled (bridge/AP mode) —
        # these values are never populated and would show "unknown" permanently.
        if desc.key in ("wan_uptime", "wan_lease") and wan_proto in ("disabled", ""):
            continue
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
    for tmpl in _BW_SENSOR_TEMPLATES:
        key_suffix = "rx" if "rx" in tmpl.key else "tx"
        entities.append(FreshTomatoSensor(coordinator, entry,
            FreshTomatoSensorDescription(
                key=f"wan_{tmpl.key}",
                name=f"WAN {tmpl.name}",
                icon=tmpl.icon,
                native_unit_of_measurement=tmpl.native_unit_of_measurement,
                device_class=tmpl.device_class,
                state_class=tmpl.state_class,
                value_fn=_make_netdev_value_fn("vlan2", key_suffix),
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
